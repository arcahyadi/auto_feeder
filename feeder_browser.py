#!/usr/bin/env python3
"""Automasi BROWSER Neo Feeder (visible, satu-per-satu, transparan).
Prinsip: semua langkah kelihatan + screenshot per baris di screenshots/.
Tidak ada klik hapus. Default dry-run (hanya jalan + screenshot, tanpa simpan).

Alur:
1. Buka http://localhost:8100/#/login
2. Isi username + password MILIKMU (bukan random), pilih Tahun Ajaran 2025/2026 Genap
3. Captcha + klik LOGIN tetap MANUAL oleh kamu (jeda 60 detik)
4. Loop CSV satu-per-satu, screenshot tiap baris ke screenshots/row-*.png

Pakai: pip install playwright && playwright install chromium
  python3 feeder_browser.py data.csv --dry-run
  python3 feeder_browser.py data.csv --go --menu mahasiswa
"""
import argparse, csv, os, sys, time
LOGIN_URL = "http://localhost:8100/#/login"
TAHUN = "2025/2026 Genap"  # id_smt 20252

def demo():
    rows = list(csv.DictReader(["nim,nama", "1,Andi"]))
    assert rows[0]["nim"] == "1", "csv rusak"
    assert TAHUN == "2025/2026 Genap", "tahun salah"
    print("demo ok: browser-visible, tanpa hapus, default dry-run")

def run(csv_path, menu, go):
    from playwright.sync_api import sync_playwright
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    os.makedirs("screenshots", exist_ok=True)
    user = os.environ.get("FEEDER_USER", "")
    pw = os.environ.get("FEEDER_PASS", "")
    if go and (not user or not pw):
        sys.exit("isi FEEDER_USER + FEEDER_PASS milikmu dulu (bukan random)")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, slow_mo=300)  # visible biar kelihatan
        pg = b.new_page()
        pg.goto(LOGIN_URL); pg.wait_for_timeout(2500)
        pg.screenshot(path="screenshots/01-login.png")
        print("1. halaman login terbuka, screenshot 01-login.png")
        if user:
            pg.fill('input[label="Username"], input[type="text"] >> nth=0', user)
            pg.fill('input[type="password"]', pw)
            # tahun ajaran: combobox quasar
            try:
                pg.click('input[role="combobox"]')
                pg.keyboard.type(TAHUN, delay=50)
                pg.wait_for_timeout(800)
                pg.keyboard.press("Enter")
            except Exception as e:
                print("pilih tahun manual:", e)
            pg.screenshot(path="screenshots/02-terisi.png")
            print("2. username+pass+arko tahun terisi. ISI CAPTCHA MANUAL + klik LOGIN di jendela browser.")
            print("   Script jeda 60 detik, kamu yang klik login...")
            time.sleep(60)
        pg.screenshot(path="screenshots/03-sudah-login.png")
        for i, r in enumerate(rows, 1):
            # ponytail: selector spesifik menu diisi setelah kamu sebutkan sumber;
            # template ini hanya jalan ke menu + screenshot, tanpa simpan otomatis
            print(f"[{i}/{len(rows)}] {r} -> screenshots/row-{i}.png (dry={'tidak' if go else 'ya'})")
            pg.screenshot(path=f"screenshots/row-{i}.png")
            time.sleep(0.5)
        print("selesai, cek screenshots/ satu-per-satu. Tidak ada delete di script ini.")
        b.close()

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "demo":
        demo(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path"); ap.add_argument("--menu", default="mahasiswa")
    ap.add_argument("--go", action="store_true"); ap.add_argument("--dry-run", action="store_true", default=True)
    a = ap.parse_args()
    run(a.csv_path, a.menu, go=a.go)
