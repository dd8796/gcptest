from flask import Flask, jsonify
import pandas as pd
import numpy as np
from google.cloud import bigquery

app = Flask(__name__)

# CONFIGURATION
RAYON_BOULE = 20
SEUIL_PSR_PROXIMITE = 2

COL_PJI = 'PJI__OF'
COL_SPOT = 'Spot_Name'
COL_PROG = 'Prog_No'
COL_DERIVE = 'Derive_Process'


# --------------------------------------------------------
# FONCTIONS METIER (INCHANGEES)
# --------------------------------------------------------

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

            paire = f"{psr_list[i]['Spot Name']} <-> {psr_list[j]['Spot Name']}"
            distances[paire] = round(d, 2)

            if d <= RAYON_BOULE:
                psr_proches.add(psr_list[i]['Spot Name'])
                psr_proches.add(psr_list[j]['Spot Name'])

    alerte = len(psr_proches) >= SEUIL_PSR_PROXIMITE
    return alerte, list(psr_proches), distances


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

    client = bigquery.Client(
        client_options={
            "scopes": [
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/cloud-platform"
            ]
        }
    )

    query_dash = "SELECT * FROM `theproject-1937.dash.dash2`"
    df_dashboard = client.query(query_dash).to_dataframe()

    query_ref = "SELECT * FROM `theproject-1937.dash.ref2`"
    df_coords = client.query(query_ref).to_dataframe()

    # Conversion coordonnées
    for col in ['X_Linx', 'Y_Linx', 'Z_Linx']:
        df_coords[col] = (
            df_coords[col]
            .astype(str)
            .str.replace(',', '.', regex=False)
            .str.strip()
        )
        df_coords[col] = pd.to_numeric(df_coords[col], errors='coerce')

    df_severe = df_dashboard[df_dashboard[COL_DERIVE] == 'Derive process severe'].copy()

    if df_severe.empty:
        return "PAS DE CONTROLE US", []

    alertes = []
    groupes = df_severe.groupby(COL_PJI)

    for pji, groupe in groupes:

        spot_names = groupe[COL_SPOT].unique()
        psr_non_vides = [psr for psr in spot_names if pd.notna(psr) and str(psr).strip() != '']

        if len(psr_non_vides) == 0:

            prog_nos = groupe[COL_PROG].dropna().unique()

            if verifier_sequences_consecutives(prog_nos):
                alertes.append({'PJI': pji, 'Type': 'Séquences consécutives'})

            continue

        df_coords_filtre = df_coords[df_coords['Spotname'].isin(psr_non_vides)]

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
                    'Distances': distances
                })

        else:

            prog_nos = groupe[COL_PROG].dropna().unique()

            if verifier_sequences_consecutives(prog_nos):
                alertes.append({'PJI': pji, 'Type': 'Séquences consécutives'})

    if alertes:
        return "CONTROLE US INDISPENSABLE", alertes

    return "PAS DE CONTROLE US", []


# --------------------------------------------------------
# ROUTES FLASK
# --------------------------------------------------------

@app.route("/")
def health():
    return "Service OK"


@app.route("/analyser")
def analyser():

    try:
        resultat, details = analyser_derive_process()

        return jsonify({
            "decision": resultat,
            "alertes": details
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
