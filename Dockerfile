# Utiliser une image légère de Python
FROM python:3.9-slim

# Définir le dossier de travail
WORKDIR /app

# Copier les fichiers de dépendances et les installer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier tout le code dans l'image
COPY . .

# Lancer l'application avec Gunicorn (plus robuste que app.run pour la prod)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app