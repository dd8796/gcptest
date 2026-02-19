import pandas as pd
import numpy as np

from google.cloud import bigquery

client = bigquery.Client()

# Remplacer pd.read_csv par une requête SQL
query_dash = "SELECT * FROM `theproject-1937.dash.DASH1`"
df_dashboard = client.query(query_dash).to_dataframe()

query_ref = "SELECT * FROM `theproject-1937.dash.reference`"
df_coords = client.query(query_ref).to_dataframe()


# Conversion des coordonnées en float
for col in ['X_Linx', 'Y_Linx', 'Z_Linx']:
    df_coords[col] = (
        df_coords[col]
        .astype(str)
        .str.replace(',', '.', regex=False)  # si décimales avec virgule
        .str.strip()
    )
    df_coords[col] = pd.to_numeric(df_coords[col], errors='coerce')


# CONFIGURATION
RAYON_BOULE = 20  # mm
SEUIL_PSR_PROXIMITE = 2

# Colonnes
COL_PJI = 'PJI / OF'
COL_SPOT = 'Spot Name'
COL_PROG = 'Prog No'
COL_DERIVE = 'Derive Process'

# --------------------------------------------------------
# CALCUL DISTANCE 3D
# --------------------------------------------------------
def calculer_distance_3d(x1, y1, z1, x2, y2, z2):
    return np.sqrt((x2 - x1)**2 + (y2 - y1)**2 + (z2 - z1)**2)

# --------------------------------------------------------
# PROXIMITÉ SPATIALE
# --------------------------------------------------------
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

            paire = f"{psr_list[i]['Spot Name']} <-> {psr_list[j]['Spot Name']}"
            distances[paire] = round(d, 2)

            if d <= RAYON_BOULE:
                psr_proches.add(psr_list[i]['Spot Name'])
                psr_proches.add(psr_list[j]['Spot Name'])

    alerte = len(psr_proches) >= SEUIL_PSR_PROXIMITE
    return alerte, list(psr_proches), distances

# --------------------------------------------------------
# SÉQUENCES CONSÉCUTIVES
# --------------------------------------------------------
def verifier_sequences_consecutives(prog_nos):

    if len(prog_nos) < 2:
        return False

    prog_nos_sorted = sorted(prog_nos)

    for i in range(len(prog_nos_sorted) - 1):
        if prog_nos_sorted[i+1] - prog_nos_sorted[i] == 1:
            return True

    return False

# --------------------------------------------------------
# LOGIQUE PRINCIPALE
# --------------------------------------------------------
def analyser_derive_process():

    df_severe = df_dashboard[df_dashboard[COL_DERIVE] == 'Derive process severe'].copy()

    if df_severe.empty:
        print("Aucun Derive process severe détecté")
        return "PAS DE CONTROLE US", None

    alertes = []
    groupes = df_severe.groupby(COL_PJI)

    for pji, groupe in groupes:

        print("\n" + "="*70)
        print(f"ANALYSE PJI/OF : {pji}")
        print("="*70)

        spot_names = groupe[COL_SPOT].unique()
        psr_non_vides = [psr for psr in spot_names if pd.notna(psr) and str(psr).strip() != '']

        # ----------------------------------------------------
        # CAS 1 : PAS DE PSR → Vérification séquences
        # ----------------------------------------------------
        if len(psr_non_vides) == 0:
            print("Aucun Spot Name valide → Vérification séquences")

            prog_nos = groupe[COL_PROG].dropna().unique()

            if verifier_sequences_consecutives(prog_nos):
                print("ALERTE : Séquences consécutives détectées")
                alertes.append({'PJI': pji, 'Type': 'Séquences consécutives'})
            else:
                print("Pas de séquences consécutives")

            continue

        # ----------------------------------------------------
        # CAS 2 : PSR DISPONIBLES → Essai coordonnées
        # ----------------------------------------------------
        df_coords_filtre = df_coords[df_coords['Spotname'].isin(psr_non_vides)]

        if not df_coords_filtre.empty and len(df_coords_filtre) >= 2:

            print("Coordonnées disponibles → Calcul des distances")

            df_calcul = df_coords_filtre[['Spotname', 'X_Linx', 'Y_Linx', 'Z_Linx']].rename(
                columns={'Spotname': 'Spot Name'}
            )

            alerte_geo, psr_proches, distances = verifier_proximite_spatiale(df_calcul)

            print("Distances calculées :")
            for k, v in distances.items():
                print(f"  {k} = {v} mm")

            if alerte_geo:
                print("ALERTE : PSR dans boule de 20 mm")
                alertes.append({
                    'PJI': pji,
                    'Type': 'Proximité spatiale',
                    'PSR_proches': psr_proches,
                    'Distances': distances
                })
            else:
                print("Pas de proximité < 20 mm → PAS D'ALERTE")

        else:
            # ----------------------------------------------------
            # FALLBACK : Coordonnées indisponibles → Séquences
            # ----------------------------------------------------
            print("Coordonnées indisponibles → Vérification séquences")

            prog_nos = groupe[COL_PROG].dropna().unique()

            if verifier_sequences_consecutives(prog_nos):
                print("ALERTE : Séquences consécutives détectées")
                alertes.append({'PJI': pji, 'Type': 'Séquences consécutives'})
            else:
                print("Pas de séquences consécutives")

    # --------------------------------------------------------
    # RESULTAT FINAL
    # --------------------------------------------------------
    print("\n" + "="*70)

    if alertes:
        print("DECISION : CONTROLE US INDISPENSABLE")
        return "CONTROLE US INDISPENSABLE", alertes

    print("DECISION : PAS DE CONTROLE US")
    return "PAS DE CONTROLE US", None


# --------------------------------------------------------
# EXECUTION
# --------------------------------------------------------
if __name__ == "__main__":

    resultat, details = analyser_derive_process()

    print("\n" + "="*70)
    print(f"DECISION FINALE : {resultat}")
    print("="*70)
