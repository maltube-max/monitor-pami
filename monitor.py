import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import os
import time
import re
import io
import requests

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

EMAIL_ORIGEN  = "marialtube@gmail.com"
EMAIL_PASS    = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_DESTINO = "cotizaciones@siprotec.com.ar"

URLS_BUSCADOR = [
    ("UGL",           "https://prestadores.pami.org.ar/result.php?c=7-5&par=2"),
    ("Nivel Central", "https://prestadores.pami.org.ar/result.php?c=7-5&par=1"),
]

PALABRAS_CLAVE = {
    "Clip Mitral":                 ["clip mitral", "mitraclip", "mitra clip"],
    "Lux Valve":                   ["lux valve", "lux value"],
    "Válvula Tricuspídea":         ["tricuspide", "tricuspidea", "valvula tricuspide"],
    "Protector Cerebral Sentinel": ["sentinel", "protector cerebral"],
    "Bioadaptador":                ["bioadaptador", "bio adaptador"],
    "Ken Valve":                   ["ken valve"],
}

def normalizar(texto):
    texto = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        texto = texto.replace(a, b)
    return texto

def detectar_productos(texto):
    texto_norm = normalizar(texto)
    encontrados = []
    for producto, variantes in PALABRAS_CLAVE.items():
        for v in variantes:
            if normalizar(v) in texto_norm:
                encontrados.append(producto)
                break
    return encontrados

def iniciar_browser():
    opciones = Options()
    opciones.add_argument("--headless")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1920,1080")
    opciones.add_argument("--lang=es-AR")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opciones)

def obtener_compras(driver, nombre, url):
    print(f"\n--- {nombre} ---")
    compras = []

    try:
        driver.get(url)
        
        # Esperar hasta 30 segundos a que desaparezca "Cargando"
        try:
            WebDriverWait(driver, 30).until(
                lambda d: "Cargando" not in d.find_element(By.TAG_NAME, "body").text
                or len(d.find_element(By.TAG_NAME, "body").text) > 2000
            )
        except:
            pass
        
        time.sleep(5)

        # Intentar seleccionar "En curso" y buscar
        try:
            select = Select(driver.find_element(By.NAME, "estado"))
            select.select_by_visible_text("En curso")
            time.sleep(1)
        except:
            pass

        try:
            btn = driver.find_element(By.XPATH,
                "//input[@type='submit'] | //button[contains(text(),'Buscar')] | //input[@value='Buscar']"
            )
            btn.click()
        except:
            pass

        # Esperar que carguen los resultados — hasta 30 segundos
        try:
            WebDriverWait(driver, 30).until(
                lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 3000
            )
        except:
            pass

        time.sleep(5)

        texto_pagina = driver.find_element(By.TAG_NAME, "body").text
        print(f"  Texto obtenido: {len(texto_pagina)} chars")
        print(f"  Primeros 500 chars: {texto_pagina[:500]}")

        # Buscar todos los links
        links = driver.find_elements(By.TAG_NAME, "a")
        links_docs = []
        for link in links:
            href = link.get_attribute("href") or ""
            if any(x in href.lower() for x in ["pdf","pliego","compra","download","archivo","ver","adjunto","doc","569","result"]):
                if href and href not in links_docs and "caducidad" not in href and "reglamento" not in href and "marco_regulatorio" not in href:
                    links_docs.append(href)

        print(f"  Links de compras: {len(links_docs)}")
        for l in links_docs:
            print(f"    {l}")

        # Buscar en texto de la página
        productos = detectar_productos(texto_pagina)
        if productos:
            info = extraer_info(texto_pagina)
            compras.append({
                "link": url, "es_pdf": False,
                "productos": productos, "info": info,
                "buscador": nombre, "pdf_bytes": None,
            })
            print(f"  ✅ En página: {', '.join(productos)}")

        # Revisar cada documento
        for href in links_docs[:30]:
            try:
                print(f"  Revisando: {href}")
                r = requests.get(href, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"
                })
                if r.status_code != 200:
                    continue
                content_type = r.headers.get("Content-Type","")
                texto_doc = ""
                bytes_doc = None
                if "pdf" in content_type.lower() or href.lower().endswith(".pdf"):
                    try:
                        from pdfminer.high_level import extract_text as pdf_extract
                        texto_doc = pdf_extract(io.BytesIO(r.content))
                        bytes_doc = r.content
                    except:
                        texto_doc = r.content.decode("latin-1", errors="ignore")
                        bytes_doc = r.content
                else:
                    texto_doc = re.sub(r'<[^>]+>', ' ', r.text)
                    texto_doc = re.sub(r'\s+', ' ', texto_doc).strip()

                productos = detectar_productos(texto_doc)
                if productos:
                    info = extraer_info(texto_doc)
                    compras.append({
                        "link": href, "es_pdf": bytes_doc is not None,
                        "productos": productos, "info": info,
                        "buscador": nombre, "pdf_bytes": bytes_doc,
                    })
                    print(f"  ✅ ENCONTRADO: {', '.join(productos)}")
                time.sleep(0.5)
            except Exception as e:
                print(f"  Error {href}: {e}")

    except Exception as e:
        print(f"  Error general: {e}")

    return compras

def extraer_info(texto):
    info = {}
    t = texto.upper() if texto else ""
    m = re.search(r'COMPULSA[^\d]*(\d+)', t)
    if m: info["numero"] = m.group(1)
    m = re.search(r'UGL[:\s#]*(\d+\s*[-–]\s*[\w\s]{3,30})', t)
    if m: info["ugl"] = m.group(1).strip()[:50]
    m = re.search(r'(\d{2}/\d{2}/\d{4})\s*[-–]?\s*8:00', t)
    if m: info["cierre"] = m.group(1)
    if texto:
        m = re.search(r'[\w.\-]+@pami\.org\.ar', texto.lower())
        if m: info["email_contacto"] = m.group(0)
    return info

def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"=== Monitor PAMI — {fecha} ===")

    driver = iniciar_browser()
    resultados = []
    adjuntos = []

    try:
        for nombre, url in URLS_BUSCADOR:
            compras = obtener_compras(driver, nombre, url)
            for c in compras:
                if c["pdf_bytes"] and len(adjuntos) < 5:
                    n = f"pliego_{c['info'].get('numero', len(adjuntos)+1)}.pdf"
                    adjuntos.append((n, c["pdf_bytes"]))
                resultados.append(c)
    finally:
        driver.quit()

    print(f"\n=== Total: {len(resultados)} resultado(s) ===")

    if resultados:
        enviar_email_con_coincidencias(resultados, adjuntos, fecha)
    else:
        enviar_email_sin_coincidencias(fecha)

ESTILO = """<style>
body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f4f6f9;margin:0;padding:0}
.wrap{max-width:640px;margin:30px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)}
.header{background:#1a5276;padding:22px 30px}.header h1{margin:0;color:#fff;font-size:20px}
.header p{margin:5px 0 0;color:#aed6f1;font-size:13px}
.banner-ok{background:#eafaf1;border-left:4px solid #27ae60;padding:14px 24px}
.banner-ok p{margin:0;color:#1e8449;font-size:15px;font-weight:600}
.banner-no{background:#fdfefe;border-left:4px solid #85929e;padding:14px 24px}
.banner-no p{margin:0;color:#5d6d7e;font-size:15px;font-weight:600}
.body{padding:24px 30px}.card{background:#f8fafb;border:1px solid #d5e8d4;border-left:5px solid #27ae60;border-radius:6px;padding:16px 20px;margin-bottom:16px}
.tag{display:inline-block;background:#eafaf1;color:#1e8449;padding:2px 10px;border-radius:4px;font-size:13px;margin:2px;font-weight:600}
.btn{display:inline-block;padding:10px 22px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;margin-top:10px;margin-right:8px}
.btn-blue{background:#1a5276;color:#fff!important}.btn-gray{background:#85929e;color:#fff!important}
.info{font-size:13px;color:#555;margin:8px 0;line-height:1.8}
.footer{background:#f4f6f9;padding:14px 30px;border-top:1px solid #e8ecf0;text-align:center}
.footer p{margin:0;font-size:11px;color:#aaa}
ul{padding-left:20px}ul li{margin:4px 0;color:#555;font-size:14px}
</style>"""

def enviar_email_con_coincidencias(resultados, adjuntos, fecha):
    cards = ""
    for r in resultados:
        tags = "".join(f'<span class="tag">🔍 {p}</span>' for p in r["productos"])
        info = r.get("info", {})
        lineas = []
        if info.get("numero"):         lineas.append(f"<b>Compulsa N°:</b> {info['numero']}")
        if info.get("ugl"):            lineas.append(f"<b>UGL:</b> {info['ugl']}")
        if info.get("cierre"):         lineas.append(f"<b>⚠️ Cierre:</b> {info['cierre']}")
        if info.get("email_contacto"): lineas.append(f"<b>Enviar cotización a:</b> {info['email_contacto']}")
        lineas.append(f"<b>Sección:</b> {r['buscador']}")
        info_html = "<br>".join(lineas)
        btn = f'<a href="{r["link"]}" class="btn btn-blue">{"📄 Ver PDF" if r["es_pdf"] else "🔗 Ver compra"}</a>'
        cards += f'<div class="card"><div style="margin-bottom:10px">{tags}</div><p class="info">{info_html}</p>{btn}</div>'
    adj_nota = f"<br><span style='font-size:13px;font-weight:normal'>📎 {len(adjuntos)} pliego(s) PDF adjunto(s)</span>" if adjuntos else ""
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header"><h1>🏥 Monitor PAMI — Siprotec</h1><p>{fecha} | Revisión automática diaria</p></div>
      <div class="banner-ok"><p>✅ <strong>{len(resultados)}</strong> compra(s) encontrada(s).{adj_nota}</p></div>
      <div class="body"><h2 style="color:#1a5276;font-size:15px;margin:0 0 16px">Compras detectadas:</h2>{cards}
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔎 PAMI UGL</a>
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=1" class="btn btn-gray">🔎 PAMI Central</a>
        </div></div>
      <div class="footer"><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""
    _enviar(f"✅ PAMI | {len(resultados)} pedido(s) encontrado(s) — {fecha}", html, adjuntos)

def enviar_email_sin_coincidencias(fecha):
    items = "".join(f"<li>{p}</li>" for p in PALABRAS_CLAVE.keys())
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header"><h1>🏥 Monitor PAMI — Siprotec</h1><p>{fecha} | Revisión automática diaria</p></div>
      <div class="banner-no"><p>ℹ️ No se encontraron pedidos relevantes hoy.</p></div>
      <div class="body"><p style="color:#555;font-size:14px">Productos monitoreados:</p><ul>{items}</ul>
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔗 PAMI UGL</a>
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=1" class="btn btn-gray">🔗 PAMI Central</a>
        </div></div>
      <div class="footer"><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""
    _enviar(f"ℹ️ PAMI | Sin pedidos relevantes — {fecha}", html)

def _enviar(asunto, html, adjuntos=None):
    msg = MIMEMultipart("mixed")
    msg["From"]    = EMAIL_ORIGEN
    msg["To"]      = EMAIL_DESTINO
    msg["Subject"] = asunto
    msg.attach(MIMEText(html, "html", "utf-8"))
    if adjuntos:
        for nombre, contenido in adjuntos:
            parte = MIMEBase("application", "octet-stream")
            parte.set_payload(contenido)
            encoders.encode_base64(parte)
            parte.add_header("Content-Disposition", f'attachment; filename="{nombre}"')
            msg.attach(parte)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_ORIGEN, EMAIL_PASS)
        server.send_message(msg)
    print(f"✅ Email enviado: {asunto}")

if __name__ == "__main__":
    main()
