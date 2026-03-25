"""
Script de migración: re-limpia las descripciones existentes en la DB
aplicando las mismas reglas de _clean_description del scraper.

Uso: python3 migrate_descriptions.py
"""
import sqlite3
import re
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "properties.db")


def clean_description(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"^Corredor Inmobiliario responsable:.*?\n", "", text, flags=re.I)
    text = re.sub(r"\bVer datos\b\.?", "", text, flags=re.I)
    text = re.sub(r"\bLEPORE SAN CRISTOBAL\b.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bLEPORE PROPIEDADES\b.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bAVISO LEGAL:.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bXINTEL.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bEsta unidad es apta para personas.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bMatr[ií]cula CPI\b.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bEn cumplimiento de las leyes vigentes\b.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bNota Importante:.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\bSITUAR\b.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip(" .\n")


def main():
    if not os.path.exists(DB_PATH):
        print(f"No se encontró la DB en {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, descripcion FROM properties").fetchall()

    updated = 0
    for row in rows:
        cleaned = clean_description(row["descripcion"])
        if cleaned != row["descripcion"]:
            conn.execute("UPDATE properties SET descripcion = ? WHERE id = ?", (cleaned, row["id"]))
            updated += 1
            print(f"  Propiedad {row['id']}: descripción actualizada")

    conn.commit()
    conn.close()
    print(f"\nListo. {updated} propiedades actualizadas de {len(rows)} totales.")


if __name__ == "__main__":
    main()
