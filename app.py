import os
import logging
from flask import Flask, jsonify
import pandas as pd
import numpy as np
from google.cloud import bigquery
from google.oauth2 import service_account

PROJECT_ID = "theproject-1937"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

BQ_TABLE_DASHBOARD = os.environ.get(
    "BQ_TABLE_DASHBOARD",
    "theproject-1937.dash.dash2_copy"
)

BQ_TABLE_COORDS = os.environ.get(
    "BQ_TABLE_COORDS",
    "theproject-1937.dash.ref2_copy"
)

RAYON_BOULE = int(os.environ.get("RAYON_BOULE", 20))  # mm
SEUIL_PSR_PROXIMITE = int(os.environ.get("SEUIL_PSR_PROXIMITE", 2))

COL_PJI    = "PJI___OF"
COL_SPOT   = "Spot_Name"
COL_PROG   = "Prog_No"
COL_DERIVE = "Derive_Process"

SERVICE_ACCOUNT_FILE = "theproject-1937-b302d42c6bb4.json"


# ========================================================
# AUTHENTIFICATION BIGQUERY
# ========================================================

client = bigquery.Client(project=PROJECT_ID)

print("✅ Connexion à BigQuery réussie")


app = Flask(__name__)


# --------------------------------------------------------
# CHARGEMENT DEPUIS BIGQUERY
# --------------------------------------------------------
def charger_donnees():
    """Charge et prépare les deux tables BigQuery."""
    logger.info("Chargement BigQuery — dashboard: %s", BQ_TABLE_DASHBOARD)
    df_dashboard = client.query(f"SELECT * FROM `{BQ_TABLE_DASHBOARD}`").to_dataframe()

    logger.info("Chargement BigQuery — coords: %s", BQ_TABLE_COORDS)
    df_coords = client.query(f"SELECT * FROM `{BQ_TABLE_COORDS}`").to_dataframe()
    # Conversion pour merger correctement
    df_dashboard[COL_PROG] = pd.to_numeric(df_dashboard[COL_PROG], errors="coerce")
    df_coords["Prog"] = pd.to_numeric(df_coords["Prog"], errors="coerce")

    # Nettoyage coordonnées (au cas où les valeurs arrivent en string avec virgule décimale)
    for col in ["X_Linx", "Y_Linx", "Z_Linx"]:
        if col in df_coords.columns:
            df_coords[col] = (
                df_coords[col]
                .astype(str)
                .str.replace(",", ".", regex=False)
                .str.strip()
            )
            df_coords[col] = pd.to_numeric(df_coords[col], errors="coerce")

    df_coords["Spotname"] = pd.to_numeric(df_coords["Spotname"], errors="coerce")

    logger.info(
        "Données chargées — dashboard: %d lignes, coords: %d lignes",
        len(df_dashboard), len(df_coords)
    )
    return df_dashboard, df_coords


# --------------------------------------------------------
# LOGIQUE MÉTIER (inchangée)
# --------------------------------------------------------
def calculer_distance_3d(x1, y1, z1, x2, y2, z2):
    return np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2)


def verifier_proximite_spatiale(psr_coords):
    psr_list   = psr_coords.to_dict("records")
    psr_proches = set()
    distances  = {}

    if len(psr_list) < 2:
        return False, [], {}

    for i in range(len(psr_list)):
        for j in range(i + 1, len(psr_list)):
            d = calculer_distance_3d(
                psr_list[i]["X_Linx"], psr_list[i]["Y_Linx"], psr_list[i]["Z_Linx"],
                psr_list[j]["X_Linx"], psr_list[j]["Y_Linx"], psr_list[j]["Z_Linx"],
            )
            paire = f"{int(psr_list[i]['Spot_Name'])} <-> {int(psr_list[j]['Spot_Name'])}"
            distances[paire] = round(d, 2)

            if d <= RAYON_BOULE:
                psr_proches.add(psr_list[i]["Spot_Name"])
                psr_proches.add(psr_list[j]["Spot_Name"])

    alerte = len(psr_proches) >= SEUIL_PSR_PROXIMITE
    return alerte, list(psr_proches), distances


def verifier_sequences_consecutives_detail(prog_nos):
    if len(prog_nos) < 2:
        return False, []

    prog_nos_sorted      = sorted(prog_nos)
    groupes_consecutifs  = []
    groupe_courant       = [prog_nos_sorted[0]]

    for i in range(1, len(prog_nos_sorted)):
        if prog_nos_sorted[i] - prog_nos_sorted[i - 1] == 1:
            groupe_courant.append(prog_nos_sorted[i])
        else:
            if len(groupe_courant) >= 2:
                groupes_consecutifs.append(groupe_courant)
            groupe_courant = [prog_nos_sorted[i]]

    if len(groupe_courant) >= 2:
        groupes_consecutifs.append(groupe_courant)

    return len(groupes_consecutifs) > 0, groupes_consecutifs


def analyser_derive_process(df_dashboard, df_coords):
    df_severe = df_dashboard[df_dashboard[COL_DERIVE] == "Derive process severe"].copy()

    if df_severe.empty:
        logger.info("Aucun Derive process severe détecté")
        return "PAS DE CONTROLE US", None

    alertes = []
    groupes = df_severe.groupby(COL_PJI)

    for pji, groupe in groupes:
        logger.info("=" * 70)
        logger.info("ANALYSE PJI/OF : %s", pji)

        # PSR présents dans le dashboard
        psr_non_vides = []
        for psr in groupe[COL_SPOT].unique():
            if pd.notna(psr) and str(psr).strip() != "":
                try:
                    psr_non_vides.append(float(psr))
                except Exception:
                    pass

        # Merge via Prog / Timer
        match_par_prog = pd.merge(
            groupe,
            df_coords[["Prog", "Timername", "Spotname", "X_Linx", "Y_Linx", "Z_Linx"]],
            left_on=["Prog_No", "Uai_Label"],
            right_on=["Prog", "Timername"],
            how="inner",
        )

        spots_via_prog = []
        for s in match_par_prog["Spotname"].dropna().unique().tolist():
            try:
                spots_via_prog.append(float(s))
            except Exception:
                pass

        psr_non_vides = list(set(psr_non_vides) | set(spots_via_prog))

        if not psr_non_vides:
            logger.info("Aucun spot trouvé même via Prog/Timer")
            continue

        logger.info("Spots retenus : %s", [int(s) for s in psr_non_vides])

        # CAS 1 : Coordonnées disponibles
        df_coords_filtre = (
            df_coords[df_coords["Spotname"].isin(psr_non_vides)]
            .drop_duplicates(subset="Spotname")
            .dropna(subset=["X_Linx", "Y_Linx", "Z_Linx"])
        )

        if not df_coords_filtre.empty and len(df_coords_filtre) >= 2:
            logger.info("Coordonnées disponibles → Calcul des distances")

            df_calcul = df_coords_filtre[["Spotname", "X_Linx", "Y_Linx", "Z_Linx"]].rename(
                columns={"Spotname": "Spot_Name"}
            )

            alerte_geo, psr_proches, distances = verifier_proximite_spatiale(df_calcul)

            for k, v in distances.items():
                logger.info("  %s = %s mm", k, v)

            if alerte_geo:
                logger.info("ALERTE : PSR dans boule de %d mm", RAYON_BOULE)
                alertes.append({
                    "PJI":        pji,
                    "Type":       "Proximité spatiale",
                    "PSR_proches": psr_proches,
                    "Distances":  distances,
                    "groupe":     groupe,
                })
            else:
                logger.info("Pas de proximité < %d mm → PAS D'ALERTE", RAYON_BOULE)

        else:
            # CAS 2 : Séquences consécutives
            logger.info("Coordonnées indisponibles → Vérification séquences consécutives")

            prog_nos = sorted(groupe[COL_PROG].dropna().unique().astype(int).tolist())

            if len(prog_nos) < 2:
                logger.info("Un seul programme → pas de vérification possible")
                continue

            logger.info("Programmes détectés : %s", prog_nos)

            alerte_seq, groupes_consecutifs = verifier_sequences_consecutives_detail(prog_nos)

            if alerte_seq:
                logger.info("ALERTE : %d séquence(s) consécutive(s)", len(groupes_consecutifs))
                alertes.append({
                    "PJI":            pji,
                    "Type":           "Séquences consécutives",
                    "Sequences":      groupes_consecutifs,
                    "Nb_sequences":   len(groupes_consecutifs),
                    "Nb_progs_total": sum(len(g) for g in groupes_consecutifs),
                    "groupe":         groupe,
                })
            else:
                logger.info("Programmes non consécutifs → PAS D'ALERTE")

    # --------------------------------------------------------
    # RÉSULTAT FINAL
    # --------------------------------------------------------
    logger.info("=" * 70)
    if alertes:
        logger.info("DECISION : CONTROLE US INDISPENSABLE")
        details_output = []

        for alerte in alertes:
            pji    = alerte["PJI"]
            groupe = alerte["groupe"]

            if alerte["Type"] == "Proximité spatiale":
                spots_en_alerte = [int(s) for s in alerte["PSR_proches"]]
            else:
                spots_en_alerte = [
                    int(s) for s in groupe[COL_SPOT].dropna().unique()
                    if str(s).strip() != ""
                ]

            groupe_enrichi = pd.merge(
                groupe,
                df_coords[["Prog", "Timername", "Spotname"]],
                left_on=["Prog_No", "Uai_Label"],
                right_on=["Prog", "Timername"],
                how="left",
            )

            lignes_affichees = set()

            for _, row in groupe_enrichi.iterrows():
                spot_val = None
                for col in ["Spot_Name", "Spotname"]:
                    raw = row.get(col)
                    if pd.notna(raw) and str(raw).strip() != "":
                        try:
                            spot_val = int(float(raw))
                            break
                        except Exception:
                            pass

                if alerte["Type"] == "Séquences consécutives" or (spot_val in spots_en_alerte):
                    uai  = row.get("Uai_Label", "N/A")
                    spot = spot_val if spot_val is not None else "N/A"
                    cle  = (int(pji), uai, spot)
                    if cle not in lignes_affichees:
                        lignes_affichees.add(cle)
                        msg = f"Dérive process sévère - Contrôle US | PJI : {int(pji)} | UAI : {uai} | Spot : {spot}"
                        logger.info(msg)
                        details_output.append({"pji": int(pji), "uai": uai, "spot": spot})

        return "CONTROLE US INDISPENSABLE", details_output

    logger.info("DECISION : PAS DE CONTROLE US")
    return "PAS DE CONTROLE US", None


# --------------------------------------------------------
# ROUTES FLASK
# --------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    """Health check pour Cloud Run."""
    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET", "POST"])
def run_analysis():
    try:
        df_dashboard, df_coords = charger_donnees()
        resultat, details = analyser_derive_process(df_dashboard, df_coords)
        return jsonify({"status": "success", "decision": resultat, "details": details}), 200
    except Exception as e:
        logger.exception("Erreur lors de l'analyse")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
