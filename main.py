import os
import logging
from flask import Flask
import pandas as pd
import numpy as np

app = Flask(__name__)

# ---------------------------------------------------------
# LOGGING (Cloud Run friendly)
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# CHARGEMENT DES DONNÉES AU DÉMARRAGE
# ---------------------------------------------------------
DASHBOARD_PATH = os.environ.get("DASHBOARD_PATH", "DASH1.csv")
COORDS_PATH = os.environ.get("COORDS_PATH", "RefPSRmodified.csv")

df_dashboard = pd.read_csv(DASHBOARD_PATH, sep=';', encoding='latin1')
df_coords = pd.read_csv(COORDS_PATH, sep=';', encoding='latin1')

for col in ['X_Linx', 'Y_Linx', 'Z_Linx']:
    df_coords[col] = (
        df_coords[col]
        .astype(str)
        .str.replace(',', '.', regex=False)
        .str.strip()
    )
    df_coords[col] = pd.to_numeric(df_coords[col], errors='coerce')

df_coords['Spotname'] = pd.to_numeric(df_coords['Spotname'], errors='coerce')

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
RAYON_BOULE = 20
SEUIL_PSR_PROXIMITE = 2
COL_PJI = 'PJI / OF'
COL_SPOT = 'Spot Name'
COL_PROG = 'Prog No'
COL_DERIVE = 'Derive Process'

# ---------------------------------------------------------
# TES FONCTIONS — STRICTEMENT INCHANGÉES
# ---------------------------------------------------------

def calculer_distance_3d(x1, y1, z1, x2, y2, z2):
    return np.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)

def verifier_proximite_spatiale(psr_coords):

    psr_list = psr_coords.to_dict('records')
    psr_proches = set()
    distances = {}

    if len(psr_list) < 2:
        return False, [], {}

    for i in range(len(psr_list)):
        for j in range(i + 1, len(psr_list)):

            d = calculer_distance_3d(
                psr_list[i]['X_Linx'], psr_list[i]['Y_Linx'], psr_list[i]['Z_Linx'],
                psr_list[j]['X_Linx'], psr_list[j]['Y_Linx'], psr_list[j]['Z_Linx']
            )

            paire = f"{int(psr_list[i]['Spot Name'])} <-> {int(psr_list[j]['Spot Name'])}"
            distances[paire] = round(d, 2)

            if d <= RAYON_BOULE:
                psr_proches.add(psr_list[i]['Spot Name'])
                psr_proches.add(psr_list[j]['Spot Name'])

    alerte = len(psr_proches) >= SEUIL_PSR_PROXIMITE
    return alerte, list(psr_proches), distances


def verifier_sequences_consecutives_detail(prog_nos):

    if len(prog_nos) < 2:
        return False, []

    prog_nos_sorted = sorted(prog_nos)
    groupes_consecutifs = []
    groupe_courant = [prog_nos_sorted[0]]

    for i in range(1, len(prog_nos_sorted)):
        if prog_nos_sorted[i] - prog_nos_sorted[i-1] == 1:
            groupe_courant.append(prog_nos_sorted[i])
        else:
            if len(groupe_courant) >= 2:
                groupes_consecutifs.append(groupe_courant)
            groupe_courant = [prog_nos_sorted[i]]

    if len(groupe_courant) >= 2:
        groupes_consecutifs.append(groupe_courant)

    return len(groupes_consecutifs) > 0, groupes_consecutifs


def analyser_derive_process():

    df_severe = df_dashboard[df_dashboard['Derive Process'] == 'Derive process severe'].copy()

    if df_severe.empty:
        return "PAS DE CONTROLE US", None

    alertes = []
    groupes = df_severe.groupby(COL_PJI)

    for pji, groupe in groupes:

        psr_non_vides = []
        for psr in groupe['Spot Name'].unique():
            if pd.notna(psr) and str(psr).strip() != '':
                try:
                    psr_non_vides.append(float(psr))
                except:
                    pass

        match_par_prog = pd.merge(
            groupe,
            df_coords[['Prog', 'Timername', 'Spotname', 'X_Linx', 'Y_Linx', 'Z_Linx']],
            left_on=['Prog No', 'Uai Label'],
            right_on=['Prog', 'Timername'],
            how='inner'
        )

        spots_via_prog = []
        for s in match_par_prog['Spotname'].dropna().unique().tolist():
            try:
                spots_via_prog.append(float(s))
            except:
                pass

        psr_non_vides = list(set(psr_non_vides) | set(spots_via_prog))

        if not psr_non_vides:
            continue

        df_coords_filtre = df_coords[df_coords['Spotname'].isin(psr_non_vides)] \
                           .drop_duplicates(subset='Spotname') \
                           .dropna(subset=['X_Linx', 'Y_Linx', 'Z_Linx'])

        if not df_coords_filtre.empty and len(df_coords_filtre) >= 2:

            df_calcul = df_coords_filtre[['Spotname', 'X_Linx', 'Y_Linx', 'Z_Linx']].rename(
                columns={'Spotname': 'Spot Name'}
            )

            alerte_geo, psr_proches, distances = verifier_proximite_spatiale(df_calcul)

            if alerte_geo:
                alertes.append({
                    'PJI': pji,
                    'Type': 'Proximité spatiale',
                    'PSR_proches': psr_proches,
                    'Distances': distances,
                    'groupe': groupe
                })

        else:

            prog_nos = sorted(groupe['Prog No'].dropna().unique().astype(int).tolist())

            if len(prog_nos) < 2:
                continue

            alerte_seq, groupes_consecutifs = verifier_sequences_consecutives_detail(prog_nos)

            if alerte_seq:
                alertes.append({
                    'PJI': pji,
                    'Type': 'Séquences consécutives',
                    'Sequences': groupes_consecutifs,
                    'Nb_sequences': len(groupes_consecutifs),
                    'Nb_progs_total': sum(len(g) for g in groupes_consecutifs),
                    'groupe': groupe
                })

    if alertes:
        return "CONTROLE US INDISPENSABLE", alertes

    return "PAS DE CONTROLE US", None


# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@app.route("/", methods=["GET"])
def run_analysis():
    try:
        resultat, details = analyser_derive_process()
        return {"status": "success", "decision": resultat}, 200
    except Exception as e:
        logger.exception("Erreur analyse")
        return {"status": "error", "message": str(e)}, 500


# ---------------------------------------------------------
# CLOUD RUN ENTRYPOINT
# ---------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
