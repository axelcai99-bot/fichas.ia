"""
Script one-shot: scrapea 5 URLs reales y las carga como fichas del usuario demo.
Ejecutar con: python seed_demo_fichas.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from db import init_db, get_connection
from services.scraper_service import ScraperService
from services.property_service import PropertyService
from repositories.property_repository import PropertyRepository

URLS = [
    "https://www.zonaprop.com.ar/propiedades/clasificado/veclapin-venta-departamento-9-ambientes-en-barrio-norte-57218290.html",
    "https://www.zonaprop.com.ar/propiedades/clasificado/veclapin-3-ambiente-con-pileta-tipo-casa-en-nunez-58641365.html",
    "https://www.zonaprop.com.ar/propiedades/clasificado/veclapin-departamento-en-palermo-chico-58722935.html",
    "https://www.zonaprop.com.ar/propiedades/clasificado/veclapin-av-del-libertador-y-ayacucho-56641053.html",
    "https://www.zonaprop.com.ar/propiedades/clasificado/veclapin-departamento-venta-colegiales-4-ambientes-58183040.html",
]

TOKENS = [
    "demo0001aabbccdd0001aabb",
    "demo0002aabbccdd0002aabb",
    "demo0003aabbccdd0003aabb",
    "demo0004aabbccdd0004aabb",
    "demo0005aabbccdd0005aabb",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def log(msg):
    print(f"  → {msg}", flush=True)

def limpiar_fichas_demo():
    with get_connection() as conn:
        conn.execute("DELETE FROM properties WHERE owner_username = 'demo'")
        conn.commit()
    print("✓ Fichas demo anteriores eliminadas")

def main():
    print("\n=== Seed demo fichas reales ===\n")
    init_db()
    limpiar_fichas_demo()

    scraper  = ScraperService()
    prop_repo = PropertyRepository()
    prop_service = PropertyService(prop_repo, BASE_DIR)

    for i, (url, token) in enumerate(zip(URLS, TOKENS), 1):
        print(f"\n[{i}/5] Scrapeando: {url[:80]}...")
        try:
            scraped = scraper.scrape_property(url, log=log)
            if not scraped:
                print(f"  ✗ No se pudo scrapear la URL {i}")
                continue

            prop_id = prop_service.save_scraped_property(
                source_url=url,
                owner_username="demo",
                agent_name="Demo FichasIA",
                agent_whatsapp="5491100000000",
                form_url="",
                scraped=scraped,
                log=log,
            )

            # Fijar el token público al predefinido para que showcase lo encuentre
            with get_connection() as conn:
                conn.execute(
                    "UPDATE properties SET public_token = ? WHERE id = ?",
                    (token, prop_id),
                )
                conn.commit()

            print(f"  ✓ Ficha {i} guardada (id={prop_id})")

        except Exception as e:
            print(f"  ✗ Error en ficha {i}: {e}")
            import traceback; traceback.print_exc()

    print("\n=== Listo! Las fichas demo están cargadas ===")
    print("Visitá /showcase para verlas\n")

if __name__ == "__main__":
    main()
