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
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opciones)

def obtener_compras(driver, nombre, url):
    print(f"\n--- {nombre} ---")
    resultados = []

    try:
        driver.get(url)
        try:
            WebDriverWait(driver, 30).until(
                lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 2000
            )
        except:
            pass
        time.sleep(5)

        # Seleccionar "En curso" y buscar
        try:
            select = Select(driver.find_element(By.NAME, "estado"))
            select.select_by_visible_text("En curso")
            time.sleep(1)
        except:
            pass
        try:
            btn = driver.find_element(By.XPATH,
                "//input[@type='submit'] | //button[contains(text(),'Buscar')] | //input[@value='Buscar']")
            btn.click()
        except:
            pass
        try:
            WebDriverWait(driver, 30).until(
                lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 3000
            )
        except:
            pass
        time.sleep(5)

        # Obtener el HTML completo de la página
        html_pagina = driver.page_source
        print(f"  HTML obtenido: {len(html_pagina)} chars")

        # Buscar TODOS los links a pliegos en el HTML
        # PAMI usa patrones como: ver_pliego.php, compras_ver, download, etc.
        patrones_pliego = [
            r'href=["\']([^"\']*(?:pliego|ver_pliego|download|adjunto|compras_ver)[^"\']*)["\']',
            r'href=["\']([^"\']*compraselectronicas\.pami\.org\.ar[^"\']*\.pdf)["\']',
            r'href=["\']([^"\']*\.pdf)["\']',
            r'onclick=["\'][^"\']*window\.open\(["\']([^"\']+)["\']',
        ]

        todos_links_pliego = []
        for patron in patrones_pliego:
            matches = re.findall(patron, html_pagina, re.IGNORECASE)
            for m in matches:
                if m not in todos_links_pliego:
                    if not m.startswith("http"):
                        m = "https://prestadores.pami.org.ar/" + m.lstrip("/")
                    todos_links_pliego.append(m)

        print(f"  Links a pliegos: {len(todos_links_pliego)}")
        for l in todos_links_pliego[:10]:
            print(f"    {l}")

        # Buscar filas de tabla que contengan palabras clave
        filas = driver.find_elements(By.TAG_NAME, "tr")
        print(f"  Total filas: {len(filas)}")

        for fila in filas:
            texto_fila = fila.text.strip()
            if len(texto_fila) < 10:
                continue

            productos = detectar_productos(texto_fila)
            if not productos:
                continue

            print(f"\n  ✅ FILA CON MATCH: {texto_fila[:300]}")

            # Extraer info de la fila
            info = {}

            # Número de compulsa (ej: 569/26)
            m = re.search(r'(\d+)/\d+', texto_fila)
            if m: info["numero"] = m.group(1)

            # UGL
            m = re.search(r'UGL\s+(?:V|I+)?\s*([\w\s]+?)(?:\n|$)', texto_fila, re.IGNORECASE)
            if m: info["ugl"] = m.group(1).strip()[:60]

            # Fecha cierre
            m = re.search(r'(\d{2}/\d{2}/\d{4})', texto_fila)
            if m: info["cierre"] = m.group(1)

            # Email
            m = re.search(r'[\w.\-]+@pami\.org\.ar', texto_fila.lower())
            if m: info["email_contacto"] = m.group(0)

            # Buscar links dentro de esta fila específica
            links_fila = fila.find_elements(By.TAG_NAME, "a")
            hrefs_fila = []
            for a in links_fila:
                href = a.get_attribute("href") or ""
                onclick = a.get_attribute("onclick") or ""
                if href and "result.php" not in href:
                    hrefs_fila.append(href)
                # Buscar en onclick
                m_onclick = re.search(r"window\.open\(['\"]([^'\"]+)['\"]", onclick)
                if m_onclick:
                    hrefs_fila.append(m_onclick.group(1))

            print(f"  Links en fila: {hrefs_fila}")

            # Buscar también en el HTML de la fila
            html_fila = fila.get_attribute("innerHTML") or ""
            links_html_fila = re.findall(r'href=["\']([^"\']+)["\']', html_fila)
            onclick_links = re.findall(r"window\.open\(['\"]([^'\"]+)['\"]", html_fila)
            todos_links_fila = hrefs_fila + onclick_links
            for l in links_html_fila:
                if l not in todos_links_fila and "result.php" not in l:
                    todos_links_fila.append(l)

            print(f"  Todos links fila: {todos_links_fila}")

            # Elegir el mejor link
            link_final = ""
            pdf_bytes = None

            for href in todos_links_fila:
                if not href.startswith("http"):
                    href = "https://prestadores.pami.org.ar/" + href.lstrip("/")
                if ".pdf" in href.lower() or "pliego" in href.lower() or "download" in href.lower():
                    link_final = href
                    break

            if not link_final and todos_links_fila:
                link_final = todos_links_fila[0]
                if not link_final.startswith("http"):
                    link_final = "https://prestadores.pami.org.ar/" + link_final.lstrip("/")

            # Intentar descargar el PDF
            if link_final:
                try:
                    r = requests.get(link_final, timeout=15, headers={
                        "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"
                    })
                    if r.status_code == 200:
                        content_type = r.headers.get("Content-Type", "")
                        if "pdf" in content_type.lower():
                            pdf_bytes = r.content
                            print(f"  PDF descargado: {len(pdf_bytes)} bytes")
                except Exception as e:
                    print(f"  Error descargando PDF: {e}")

            resultados.append({
                "texto_fila": texto_fila[:300],
                "productos":  productos,
                "info":       info,
                "link":       link_final,
                "es_pdf":     pdf_bytes is not None,
                "pdf_bytes":  pdf_bytes,
                "buscador":   nombre,
            })

    except Exception as e:
        print(f"  Error general: {e}")

    return resultados

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

    # Deduplicar por número de compulsa
    vistos = set()
    resultados_unicos = []
    for r in resultados:
        key = r["info"].get("numero", r["texto_fila"][:50])
        if key not in vistos:
            vistos.add(key)
            resultados_unicos.append(r)

    print(f"\n=== Total: {len(resultados_unicos)} resultado(s) ===")

    if resultados_unicos:
        enviar_email_con_coincidencias(resultados_unicos, adjuntos, fecha)
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
        if r.get("texto_fila"):        lineas.append(f"<b>Detalle:</b> {r['texto_fila'][:200]}")
        lineas.append(f"<b>Sección:</b> {r['buscador']}")
        info_html = "<br>".join(lineas)
        btn = ""
        if r.get("link"):
            label = "📄 Descargar PDF" if r["es_pdf"] else "🔗 Ver compra"
            btn = f'<a href="{r["link"]}" class="btn btn-blue">{label}</a>'
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
