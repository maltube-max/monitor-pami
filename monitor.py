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
from webdriver_manager.chrome import ChromeDriverManager

EMAIL_ORIGEN  = "marialtube@gmail.com"
EMAIL_PASS    = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_DESTINO = "cotizaciones@siprotec.com.ar"

URLS_BUSCADOR = [
    ("UGL",           "https://prestadores.pami.org.ar/result.php?c=7-5&par=2", "2"),
    ("Nivel Central", "https://prestadores.pami.org.ar/result.php?c=7-5&par=1", "1"),
]

PALABRAS_CLAVE = {
    "Clip Mitral":                 ["clip mitral", "mitraclip", "mitra clip", "clips mitrales", "reparacion valvular mitral", "reparacion percutanea valvular mitral", "reparacion mitral", "jensclip", "cierre borde a borde"],
    "Lux Valve":                   ["lux valve", "lux value"],
    "Válvula Tricuspídea":         ["tricuspide", "tricuspidea", "valvula tricuspide"],
    "Protector Cerebral Sentinel": ["sentinel", "protector cerebral", "filtro proteccion embolica", "filtro embolica", "filtro embolico", "proteccion embolica", "protección embólica", "filtro de proteccion", "sistema de proteccion cerebral", "proteccion cerebral bicarotideo", "sistema proteccion cerebral", "proteccion cerebral"],
    "Bioadaptador":                ["bioadaptador", "bio adaptador"],
    "Ken Valve":                   ["ken valve"],
    "Cierre Percutáneo":           ["manta", "proglide", "prostar", "obtura", "clothoid", "cierre percutaneo", "dispositivo de cierre percutaneo", "dispositivo de cierre vascular", "cierre vascular"],

}

IGNORAR_SI_CONTIENE = [
    "seleccione", "tipo de compra", "fecha desde", "fecha hasta",
    "aplicación de legislación", "compras menores", "concurso abreviado",
    "licitación publica", "contratación directa", "descripción\nfecha"
]

def normalizar(texto):
    texto = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        texto = texto.replace(a, b)
    return texto

def es_fila_formulario(texto):
    texto_norm = normalizar(texto)
    for ignorar in IGNORAR_SI_CONTIENE:
        if ignorar in texto_norm:
            return True
    return False

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
    # Configurar carpeta de descargas
    prefs = {"download.default_directory": "/tmp/pami_downloads",
             "download.prompt_for_download": False,
             "plugins.always_open_pdf_externally": True}
    opciones.add_experimental_option("prefs", prefs)
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opciones)

def obtener_url_desde_onclick(onclick_str):
    """Extrae la URL de un string onclick de PAMI."""
    if not onclick_str:
        return ""
    # Patrones comunes en PAMI
    patrones = [
        r"window\.open\(['\"]([^'\"]+)['\"]",
        r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
        r"href\s*=\s*['\"]([^'\"]+)['\"]",
        r"['\"]([^'\"]*(?:pliego|ver|download|pdf|compra)[^'\"]*)['\"]",
    ]
    for patron in patrones:
        m = re.search(patron, onclick_str, re.IGNORECASE)
        if m:
            url = m.group(1)
            if not url.startswith("http"):
                url = "https://prestadores.pami.org.ar/" + url.lstrip("/")
            return url
    return ""

def obtener_compras(driver, nombre, url, par):
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

        filas = driver.find_elements(By.TAG_NAME, "tr")
        print(f"  Total filas: {len(filas)}")

        for i, fila in enumerate(filas):
            texto_fila = fila.text.strip()
            if len(texto_fila) < 10:
                continue
            if es_fila_formulario(texto_fila):
                continue

            # Buscar el ID de verArchivos en esta fila
            id_archivo = ""
            elementos_onclick = fila.find_elements(By.XPATH, ".//*[@onclick]")
            for elem in elementos_onclick:
                onclick = elem.get_attribute("onclick") or ""
                m_id = re.search(r"verArchivos\(\'(\d+)\'\)", onclick)
                if m_id:
                    id_archivo = m_id.group(1)
                    break

            # Buscar palabras clave en el texto de la fila
            productos = detectar_productos(texto_fila)

            # Si no encontro en la fila pero tiene ID, leer el documento
            if not productos and id_archivo:
                try:
                    # Abrir la pagina de archivos para obtener el link real al PDF
                    url_archivos = f"https://prestadores.pami.org.ar/compras_ver_archivos.php?id={id_archivo}"
                    r_archivos = requests.get(url_archivos, timeout=15, headers={
                        "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"
                    })
                    texto_buscar = ""
                    if r_archivos.status_code == 200:
                        html_archivos = r_archivos.text
                        # Buscar links a PDFs en la pagina
                        links_pdf = re.findall(r'https://institucional[.]pami[.]org[.]ar[^\s"]*[.]pdf', html_archivos, re.IGNORECASE)
                        for link_pdf in links_pdf:
                            if not link_pdf.startswith("http"):
                                link_pdf = "https://institucional.pami.org.ar/" + link_pdf.lstrip("/")
                            try:
                                r_pdf = requests.get(link_pdf, timeout=15, headers={
                                    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"
                                })
                                if r_pdf.status_code == 200:
                                    ct = r_pdf.headers.get("Content-Type","")
                                    if "pdf" in ct.lower():
                                        try:
                                            from pdfminer.high_level import extract_text as pdf_extract
                                            texto_buscar = pdf_extract(io.BytesIO(r_pdf.content))
                                        except:
                                            texto_buscar = r_pdf.content.decode("latin-1", errors="ignore")
                                    else:
                                        texto_buscar = re.sub(r'<[^>]+>', ' ', r_pdf.text)
                                    break
                            except:
                                pass
                        # Si no encontro PDF, buscar en el HTML de la pagina de archivos
                        if not texto_buscar:
                            texto_buscar = re.sub(r'<[^>]+>', ' ', html_archivos)
                    
                    if texto_buscar:
                        texto_buscar = re.sub(r'\s+', ' ', texto_buscar).strip()
                        productos = detectar_productos(texto_buscar)
                        if productos:
                            print(f"  Encontrado en documento {id_archivo}: {', '.join(productos)}")
                            texto_fila = texto_fila + " " + texto_buscar[:500]
                except Exception as e:
                    print(f"  Error leyendo doc {id_archivo}: {e}")

            if not productos:
                continue

            print(f"\n  ✅ MATCH fila {i}: {texto_fila[:200]}")

            # Extraer info
            nro = ""
            ejercicio = ""
            expediente = ""
            cierre = ""
            ugl = ""

            m = re.search(r'(\d+)/(\d+)', texto_fila)
            if m:
                nro = m.group(1)
                ejercicio = "20" + m.group(2) if len(m.group(2)) == 2 else m.group(2)

            m = re.search(r'\b(\d{8,})\b', texto_fila)
            if m:
                expediente = m.group(1)

            m = re.search(r'(\d{2}/\d{2}/\d{4})', texto_fila)
            if m:
                cierre = m.group(1)

            m = re.search(r'UGL\s+(?:V+|I+|X+)?\s*([\w\s]+?)(?:\s\d{4}|\n|$)', texto_fila, re.IGNORECASE)
            if m:
                ugl = m.group(1).strip()[:60]

            desc = ""
            m = re.search(r'((?:VALVULA|CLIP|SENTINEL|BIOADAPT|KEN|LUX)[\w\s\-–,/]+?)(?:\d{2}/\d{2}/\d{4}|remove_red_eye|$)', texto_fila, re.IGNORECASE)
            if m:
                desc = m.group(1).strip()[:150]

            # Buscar el ícono del ojo (remove_red_eye) en la fila
            # y obtener su URL via onclick o href
            link_ojo = ""
            link_doc = ""
            pdf_bytes = None

            # Buscar todos los elementos clickeables en la fila
            elementos_clickeables = fila.find_elements(By.XPATH, 
                ".//*[@onclick] | .//a[@href] | .//i | .//span[@class] | .//button"
            )
            
            for elem in elementos_clickeables:
                tag = elem.tag_name
                clase = elem.get_attribute("class") or ""
                onclick = elem.get_attribute("onclick") or ""
                href = elem.get_attribute("href") or ""
                texto_elem = elem.text.strip()
                
                print(f"    Elem: tag={tag} class={clase} text={texto_elem} onclick={onclick[:100]} href={href[:100]}")
                
                # El ícono del ojo en Material Icons se llama "remove_red_eye" o "visibility"
                if any(x in clase.lower() for x in ["eye", "ver", "visibility", "remove_red"]) or \
                   any(x in texto_elem.lower() for x in ["remove_red_eye", "visibility"]) or \
                   "remove_red_eye" in onclick:
                    url_ojo = obtener_url_desde_onclick(onclick) or href
                    if url_ojo:
                        link_ojo = url_ojo
                        print(f"    ✅ Link ojo: {link_ojo}")

                # El ícono del documento se llama "description" en Material Icons
                if any(x in clase.lower() for x in ["description", "doc", "file", "pdf"]) or \
                   any(x in texto_elem.lower() for x in ["description"]) or \
                   "description" in onclick:
                    url_doc = obtener_url_desde_onclick(onclick) or href
                    if url_doc:
                        link_doc = url_doc
                        print(f"    ✅ Link doc: {link_doc}")

                # Cualquier onclick con URL
                if onclick and not link_ojo:
                    url_onclick = obtener_url_desde_onclick(onclick)
                    if url_onclick and "result.php" not in url_onclick:
                        link_ojo = url_onclick
                        print(f"    ✅ Link onclick genérico: {link_ojo}")

            # Usar link_ojo primero, si no link_doc
            link_final = link_ojo or link_doc

            # Si encontramos un link, intentar descargar
            if link_final:
                try:
                    r = requests.get(link_final, timeout=20, headers={
                        "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"
                    })
                    if r.status_code == 200:
                        ct = r.headers.get("Content-Type", "")
                        if "pdf" in ct.lower() or "word" in ct.lower() or "octet" in ct.lower() or len(r.content) > 10000:
                            pdf_bytes = r.content
                            print(f"  ✅ Archivo descargado: {len(pdf_bytes)} bytes, tipo: {ct}")
                except Exception as e:
                    print(f"  Error descargando: {e}")

            # Si no pudimos descargar, al menos guardamos el link
            if not link_final:
                link_final = url  # fallback al buscador

            resultados.append({
                "desc":      desc,
                "productos": productos,
                "info": {
                    "numero":     nro,
                    "ejercicio":  ejercicio,
                    "expediente": expediente,
                    "ugl":        ugl,
                    "cierre":     cierre,
                },
                "link":      link_final,
                "es_pdf":    pdf_bytes is not None,
                "pdf_bytes": pdf_bytes,
                "buscador":  nombre,
            })

    except Exception as e:
        print(f"  Error general: {e}")

    return resultados

def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"=== Monitor PAMI — {fecha} ===")

    # Crear carpeta de descargas
    os.makedirs("/tmp/pami_downloads", exist_ok=True)

    driver = iniciar_browser()
    resultados = []
    adjuntos = []

    try:
        for nombre, url, par in URLS_BUSCADOR:
            compras = obtener_compras(driver, nombre, url, par)
            for c in compras:
                if c["pdf_bytes"] and len(adjuntos) < 5:
                    ext = "pdf"
                    n = f"pliego_{c['info'].get('numero', len(adjuntos)+1)}.{ext}"
                    adjuntos.append((n, c["pdf_bytes"]))
                resultados.append(c)
    finally:
        driver.quit()

    # Deduplicar
    vistos = set()
    resultados_unicos = []
    for r in resultados:
        key = r["info"].get("numero","") + r["info"].get("expediente","")
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
        if info.get("numero"):     lineas.append(f"<b>Compulsa N°:</b> {info['numero']}/{info.get('ejercicio','')}")
        if info.get("ugl"):        lineas.append(f"<b>UGL:</b> {info['ugl']}")
        if info.get("cierre"):     lineas.append(f"<b>⚠️ Cierre:</b> {info['cierre']}")
        if info.get("expediente"): lineas.append(f"<b>Expediente:</b> EX-{info.get('ejercicio','')}-{info['expediente']}-INSSJP")
        if r.get("desc"):          lineas.append(f"<b>Descripción:</b> {r['desc']}")
        info_html = "<br>".join(lineas)

        if r.get("es_pdf"):
            btn = f'<a href="{r["link"]}" class="btn btn-blue">📄 Ver pliego</a>'
        elif r.get("link") and "result.php" not in r["link"]:
            btn = f'<a href="{r["link"]}" class="btn btn-blue">🔗 Ver pliego</a>'
        else:
            btn = f'<a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔗 Ver en PAMI</a>'

        cards += f'<div class="card"><div style="margin-bottom:10px">{tags}</div><p class="info">{info_html}</p>{btn}</div>'

    adj_nota = f"<br><span style='font-size:13px;font-weight:normal'>📎 {len(adjuntos)} pliego(s) adjunto(s) al email</span>" if adjuntos else ""
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
