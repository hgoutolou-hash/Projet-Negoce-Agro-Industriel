import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

# --- Configuration de la base de données MySQL ---
DB_USER = "root"
DB_PASSWORD = "PASSWORD"  # Laissez vide si aucun mot de passe, ou renseignez le vôtre
DB_HOST = "localhost"
DB_PORT = "3306"
DB_NAME = "negoce_agro_db"

# --- Chemin du fichier Excel source ---
EXCEL_FILE = "Projet_Negoce_Agro_Industriel.xlsx"

# --- Création de la base si elle n'existe pas encore ---
_server_engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/")
with _server_engine.connect() as _conn:
    _conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    _conn.commit()
_server_engine.dispose()

engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")


def normalize_columns(df):
    """Standardisation universelle des en-têtes + valeurs nulles textuelles -> NaN."""
    df = df.copy()
    df.columns = (
        df.columns.str.strip().str.lower()
        .str.replace("é", "e").str.replace("è", "e").str.replace("à", "a")
        .str.replace("ô", "o").str.replace("û", "u").str.replace("â", "a")
        .str.replace(" ", "_")
    )
    df = df.replace(["nan", "None", ""], np.nan)
    return df


# ---------------------------------------------------------------------------
# Consolidation des entités dupliquées (Fournisseurs, Clients)
#
# Le fichier source contient une ligne par (entité + variante aléatoire) au
# lieu d'une ligne par entité réelle. On regroupe par "nom de base" (nom sans
# le suffixe numérique), on choisit des attributs canoniques stables, et on
# produit un dictionnaire de correspondance {ancien_id: nouvel_id} pour
# corriger ensuite les clés étrangères dans Achats et Ventes.
# ---------------------------------------------------------------------------

def consolidate_fournisseurs(df):
    df = df.copy()
    df["_base_name"] = (
        df["nom_fournisseur"].str.strip().str.replace(r"\s+F\d+$", "", regex=True).str.strip()
    )
    canonical_rows, bridge_rows, id_mapping = [], [], {}
    for i, (base_name, group) in enumerate(sorted(df.groupby("_base_name")), start=1):
        new_id = f"FRN{i:03d}"
        for old_id in group["id_fournisseur"]:
            id_mapping[old_id] = new_id
        canonical_rows.append({
            "id_fournisseur": new_id,
            "nom_fournisseur": base_name,
            "ville": group["ville"].mode().iloc[0],
            "pays": group["pays"].mode().iloc[0],
            "delai_moyen_livraison": round(group["delai_moyen_livraison"].mean()),
        })
        for produit in group["produit_fourni"].dropna().unique():
            bridge_rows.append({"id_fournisseur": new_id, "produit_fourni": produit})
    return pd.DataFrame(canonical_rows), pd.DataFrame(bridge_rows), id_mapping


def consolidate_clients(df):
    df = df.copy()
    df["_base_name"] = (
        df["nom_client"].str.strip().str.replace(r"\s+\d+$", "", regex=True).str.strip()
    )
    canonical_rows, id_mapping = [], {}
    for i, (base_name, group) in enumerate(sorted(df.groupby("_base_name")), start=1):
        new_id = f"CLI{i:03d}"
        for old_id in group["id_client"]:
            id_mapping[old_id] = new_id
        canonical_rows.append({
            "id_client": new_id,
            "nom_client": base_name,
            "type_client": group["type_client"].mode().iloc[0],
            "secteur_activite": group["secteur_activite"].mode().iloc[0],
            "ville": group["ville"].mode().iloc[0],
            "pays": group["pays"].mode().iloc[0],
            "date_creation": pd.to_datetime(group["date_creation"], format="mixed", errors="coerce").min(),
        })
    df_clients = pd.DataFrame(canonical_rows)

    # Client générique pour les ventes orphelines (ex: référence client absente
    # de la table Clients). On l'ajoute explicitement au lieu de laisser
    # Power BI l'afficher comme un segment "(Blank)" — le CA associé reste
    # ainsi comptabilisé, mais rattaché à un profil identifiable.
    df_clients = pd.concat([df_clients, pd.DataFrame([{
        "id_client": "CLI999",
        "nom_client": "Client Standard Divers",
        "type_client": "Générique",
        "secteur_activite": "Divers",
        "ville": "Non renseigné",
        "pays": "Non renseigné",
        "date_creation": pd.NaT,
    }])], ignore_index=True)

    return df_clients, id_mapping


def clean_sheet(df, sheet_name, fournisseur_mapping=None, client_mapping=None):
    """Nettoie, filtre et standardise un DataFrame pour un onglet spécifique."""
    df = normalize_columns(df)

    if sheet_name == "Ventes":
        df["date_vente"] = pd.to_datetime(df["date_vente"], format="mixed", errors="coerce")
        df["remise"] = pd.to_numeric(df["remise"], errors="coerce").fillna(0.0)
        df["chiffre_affaires"] = round(df["quantite"] * df["prix_unitaire"] * (1 - df["remise"]), 2)
        df = df.drop_duplicates(subset=["id_vente"], keep="first")
        if client_mapping:
            df["id_client"] = df["id_client"].map(client_mapping).fillna(df["id_client"])

    elif sheet_name == "Produits":
        df["produit"] = df["produit"].replace({"Maiz": "Maïs", "Noix de Cajouuu": "Noix de cajou"})
        df = df.drop_duplicates(subset=["id_produit"], keep="first")

    elif sheet_name == "Agences":
        df = df.drop_duplicates(subset=["id_agence"], keep="first")

    elif sheet_name == "Entrepôts":
        df = df.drop_duplicates(subset=["id_entrepot"], keep="first")

    elif sheet_name == "Achats":
        df["date_achat"] = pd.to_datetime(df["date_achat"], format="mixed", errors="coerce")
        df["montant_total"] = df["montant_total"].abs()
        df = df.drop_duplicates(subset=["id_achat"], keep="first")
        if fournisseur_mapping:
            df["id_fournisseur"] = df["id_fournisseur"].map(fournisseur_mapping).fillna(df["id_fournisseur"])

    elif sheet_name == "Dépenses":
        df["date_depense"] = pd.to_datetime(df["date_depense"], format="mixed", errors="coerce")
        df["montant"] = df["montant"].abs()
        df = df.drop_duplicates(subset=["id_depense"], keep="first")

    elif sheet_name == "Stocks":
        df["date_stock"] = pd.to_datetime(df["date_stock"], format="mixed", errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------
print("Début de l'ingestion et du nettoyage des données...")
print("-" * 60)

xls = pd.ExcelFile(EXCEL_FILE)

# 1) Consolider Fournisseurs et Clients avant de traiter les tables de faits
df_fournisseurs_raw = normalize_columns(pd.read_excel(xls, sheet_name="Fournisseurs"))
df_fournisseurs, df_fournisseurs_produits, fournisseur_mapping = consolidate_fournisseurs(df_fournisseurs_raw)

df_clients_raw = normalize_columns(pd.read_excel(xls, sheet_name="Clients"))
df_clients, client_mapping = consolidate_clients(df_clients_raw)

print(f"✔️ Fournisseurs consolidés : {len(df_fournisseurs_raw)} lignes -> {len(df_fournisseurs)} fournisseurs réels")
print(f"✔️ Table de liaison Fournisseurs_Produits créée : {len(df_fournisseurs_produits)} lignes")
print(f"✔️ Clients consolidés : {len(df_clients_raw)} lignes -> {len(df_clients)} clients réels")
print("-" * 60)

# 2) Chargement de Fournisseurs / Fournisseurs_Produits / Clients déjà nettoyés
df_fournisseurs.to_sql(name="fournisseurs", con=engine, if_exists="replace", index=False)
print(f"✔️ Table 'fournisseurs' injectée avec succès ({len(df_fournisseurs)} lignes validées).")

df_fournisseurs_produits.to_sql(name="fournisseurs_produits", con=engine, if_exists="replace", index=False)
print(f"✔️ Table 'fournisseurs_produits' injectée avec succès ({len(df_fournisseurs_produits)} lignes validées).")

df_clients.to_sql(name="clients", con=engine, if_exists="replace", index=False)
print(f"✔️ Table 'clients' injectée avec succès ({len(df_clients)} lignes validées).")

# 3) Traitement standard des autres onglets
for sheet in xls.sheet_names:
    if sheet in ("Fournisseurs", "Clients"):
        continue

    table_name = (
        sheet.strip().lower()
        .replace("é", "e").replace("è", "e").replace("à", "a")
        .replace("ô", "o").replace("û", "u").replace("â", "a")
        .replace(" ", "_")
    )

    df_raw = pd.read_excel(xls, sheet_name=sheet)
    df_clean = clean_sheet(
        df_raw, sheet,
        fournisseur_mapping=fournisseur_mapping,
        client_mapping=client_mapping,
    )

    df_clean.to_sql(name=table_name, con=engine, if_exists="replace", index=False)
    print(f"✔️ Table '{table_name}' injectée avec succès ({len(df_clean)} lignes validées).")

print("-" * 60)
print("Traitement terminé ! Toutes les tables sont nettoyées, consolidées et chargées dans MySQL.")
