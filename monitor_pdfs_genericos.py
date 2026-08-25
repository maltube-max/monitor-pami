"""
PROCESO B — Monitor PAMI: lectura de compulsas con descripcion generica.

IMPORTANTE: este script es COMPLETAMENTE INDEPENDIENTE del monitor.py diario
(Proceso A). Tiene su propio navegador, su propia sesion, su propio email.
Si este script falla, se cuelga o tarda demasiado, NO AFECTA al Proceso A
de ninguna manera - son ejecuciones separadas en pasos/workflows distintos.

Que hace:
1. Escanea UGL y Nivel Central igual que el Proceso A
2. Ignora todas las filas que ya matchean por texto (esas ya las cubre el
   Proceso A)
3. De las filas SIN match, se queda solo con las que tienen descripcion
   generica (ej. "CIRUGIA INTERVENCIONISTA", "ADQUISICION DE INSUMOS")
4. Para esas (con tope duro de clicks y de tiempo total), clickea el icono
   real de "ver archivos" y lee lo que PAMI efectivamente muestra
5. Si encuentra palabras clave adentro, arma un email de alerta aparte

Limites de seguridad (para que nunca vuelva a descontrolarse):
- MAX_CLICKS: tope duro de intentos de lectura de documento en TOTAL
  (sumando UGL + Nivel Central)
- MAX_MINUTOS: si se supera este tiempo total, corta la busqueda ahi mismo
  y manda lo que haya encontrado hasta el momento
- Todo el proceso de click esta en try/except: un fallo puntual no corta
  la corrida
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
    ("UGL",           "https://prestadores.pami.org.ar/result.php?c=7-5&par=2"),
    ("Nivel Central", "https://prestadores.pami.org.ar/result.php?c=7-5&par=1"),
]

PALABRAS_CLAVE = {
    "Clip Mitral":                 ["clip mitral", "mitraclip", "mitra clip", "clips mitrales", "reparacion valvular mitral", "reparacion percutanea valvular mitral", "reparacion mitral", "jensclip", "cierre borde a borde"],
    "Lux Valve":                   ["lux valve", "lux value"],
    "Válvula Tricuspídea":         ["tricuspide", "tricuspidea", "valvula tricuspide"],
    "Protector Cerebral Sentinel": ["sentinel", "protector cerebral", "filtro proteccion embolica", "filtro embolica", "filtro embolico", "proteccion embolica", "protección embólica", "filtro de proteccion", "sistema de proteccion cerebral", "proteccion cerebral bicarotideo", "sistema proteccion cerebral", "proteccion cerebral", "filtro de proteccion cerebral"],
    "Bioadaptador":                ["bioadaptador", "bio adaptador"],
    "Ken Valve":                   ["ken valve"],
    "Cierre Percutáneo":           ["manta", "proglide", "prostar", "obtura", "clothoid", "cierre percutaneo", "dispositivo de cierre percutaneo", "dispositivo de cierre vascular", "cierre vascular"],
}

IGNORAR_SI_CONTIENE = [
    "seleccione", "tipo de compra", "fecha desde", "fecha hasta",
    "aplicación de legislación", "compras menores", "concurso abreviado",
    "licitación publica", "contratación directa", "descripción\nfecha"
]

PATRONES_GENERICOS = [
    "cirugia intervencionista", "cirugía intervencionista",
    "adquisicion de insumos", "adquisición de insumos",
    "compra menor", "dispositivo para", "prestacion de servicio",
    "prestación de servicio", "insumos de cardiologia", "insumos de cardiología",
    "insumos de", "material descartable", "equipamiento medico",
]

# --- LIMITES DE SEGURIDAD ---
MAX_CLICKS = 15          # tope duro de documentos a intentar leer, en total
MAX_MINUTOS = 12         # tope duro de tiempo total dedicado a clicks

HEADERS_HTTP = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}


def normalizar(texto):
    texto = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        texto = texto.replace(a, b)
    return texto

def es_fila_formulario(texto):
    texto_norm = normalizar(texto)
    return any(normalizar(x) in texto_norm for x in IGNORAR_SI_CONTIENE)

def unir_letras_sueltas(texto):
    """Arregla un problema comun de pdfminer: en algunas tablas/fuentes,
    el texto se extrae con las letras separadas por espacios
    (ej. 'S E N T I N E L' en vez de 'SENTINEL'), lo que hace que la
    busqueda de palabras clave nunca encuentre nada aunque el texto
    este ahi. Esto une esas letras sueltas antes de buscar."""
    return re.sub(
        r'\b(?:[A-Za-zÁÉÍÓÚÑáéíóúñ] ){2,}[A-Za-zÁÉÍÓÚÑáéíóúñ]\b',
        lambda m: m.group(0).replace(' ', ''),
        texto
    )

def detectar_productos(texto):
    texto = unir_letras_sueltas(texto)
    texto_norm = normalizar(texto)
    encontrados = []
    for producto, variantes in PALABRAS_CLAVE.items():
        for v in variantes:
            if normalizar(v) in texto_norm:
                encontrados.append(producto)
                break
    return encontrados

def es_descripcion_generica(texto):
    texto_norm = normalizar(texto)
    return any(normalizar(p) in texto_norm for p in PATRONES_GENERICOS)

def iniciar_browser():
    opciones = Options()
    opciones.add_argument("--headless")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1920,1080")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opciones)

def extraer_links_documento(html, base_url):
    links = set()
    for m in re.finditer(
        r'https?://(?:institucional|prestadores|www)[.]pami[.]org[.]ar[^\s"\'<>]*\.(?:pdf|docx?|PDF|DOCX?)',
        html
    ):
        links.add(m.group(0))
    for m in re.finditer(r'(?:href|src)\s*=\s*["\']([^"\']+\.(?:pdf|docx?|PDF|DOCX?))["\']', html):
        url = m.group(1)
        if not url.startswith("http"):
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = "https://institucional.pami.org.ar" + url
            else:
                url = base_url.rsplit("/", 1)[0] + "/" + url
        links.add(url)
    return list(links)

def _descargar_y_extraer(url, cookies):
    try:
        r = requests.get(url, cookies=cookies, timeout=15, headers=HEADERS_HTTP)
    except Exception as e:
        print(f"    [B] error descargando {url}: {e}")
        return ""
    if r.status_code != 200:
        return ""
    ct = r.headers.get("Content-Type", "").lower()
    contenido = r.content
    if "pdf" in ct or url.lower().endswith(".pdf"):
        try:
            from pdfminer.high_level import extract_text as pdf_extract
            return pdf_extract(io.BytesIO(contenido))
        except Exception as e:
            print(f"    [B] fallo pdfminer: {e}")
            return ""
    if "word" in ct or url.lower().endswith((".doc", ".docx")):
        try:
            import docx
            doc = docx.Document(io.BytesIO(contenido))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            return ""
    if len(contenido) > 500:
        return re.sub(r'<[^>]+>', ' ', r.text)
    return ""

def leer_documento_via_click(driver, elem_trigger, numero="", ejercicio=""):
    """Clickea el icono real y lee lo que PAMI efectivamente muestra.
    Si se pasan numero/ejercicio, prioriza el link que corresponde
    especificamente a esta compulsa (patron CAB_{numero}_{ejercicio}_...)
    en vez de agarrar cualquier PDF de la pagina (reglamento general,
    actas viejas, etc.)."""
    texto = ""
    ventanas_antes = driver.window_handles
    ventana_original = driver.current_window_handle

    try:
        driver.execute_script("arguments[0].scrollIntoView(true);", elem_trigger)
        driver.execute_script("arguments[0].click();", elem_trigger)
    except Exception as e:
        print(f"    [B] error al clickear: {e}")
        return ""

    time.sleep(1.5)

    try:
        ventanas_despues = driver.window_handles
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}

        if len(ventanas_despues) > len(ventanas_antes):
            nueva = [w for w in ventanas_despues if w not in ventanas_antes][0]
            driver.switch_to.window(nueva)
            try:
                WebDriverWait(driver, 8).until(
                    lambda d: d.execute_script("return document.readyState") == "complete")
            except Exception:
                pass
            url_actual = driver.current_url
            print(f"    [B] pestaña nueva: {url_actual}")

            if ".pdf" in url_actual.lower():
                texto = _descargar_y_extraer(url_actual, cookies)
            else:
                html = driver.page_source
                links_doc = ordenar_priorizando_compulsa(
                    extraer_links_documento(html, url_actual), numero, ejercicio)
                for link in links_doc:
                    texto = _descargar_y_extraer(link, cookies)
                    if texto:
                        break
                if not texto:
                    texto = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)).strip()

            try:
                driver.close()
            except Exception:
                pass
            driver.switch_to.window(ventana_original)
        else:
            print(f"    [B] no se abrió pestaña nueva (modal o AJAX en la misma página)")
            html = driver.page_source
            print(f"    [B] largo del HTML actual: {len(html)}")

            links_doc = extraer_links_documento(html, driver.current_url)
            links_doc = ordenar_priorizando_compulsa(links_doc, numero, ejercicio)
            print(f"    [B] links a documento, ya priorizados: {links_doc}")

            for link in links_doc:
                texto = _descargar_y_extraer(link, cookies)
                if texto:
                    print(f"    [B] usando link: {link}")
                    break

            if not texto:
                texto_plano = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)).strip()
                print(f"    [B] sin link descargable, uso texto plano de la pagina: {len(texto_plano)} caracteres")
                if len(texto_plano) > 100:
                    texto = texto_plano

            try:
                from selenium.webdriver.common.keys import Keys
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass
    except Exception as e:
        print(f"    [B] error inesperado leyendo documento: {e}")
        try:
            if driver.current_window_handle != ventana_original:
                driver.close()
                driver.switch_to.window(ventana_original)
        except Exception:
            pass

    return texto

def ordenar_priorizando_compulsa(links, numero, ejercicio):
    """Pone primero los links que contienen el patron CAB_{numero}_{ejercicio}
    (el pliego especifico de esta compulsa), y descarta los que son
    claramente genericos (reglamento, marco regulatorio)."""
    if not links:
        return links
    prioritarios = []
    resto = []
    for link in links:
        low = link.lower()
        if "reglamento" in low or "marco_regulatorio" in low or "anexos-r-" in low:
            continue  # nunca sirven, son documentos generales de PAMI
        if numero and ejercicio and f"cab_{numero}_{ejercicio}" in low.replace("-", "_"):
            prioritarios.append(link)
        else:
            resto.append(link)
    return prioritarios + resto

def cargar_tabla(driver, url):
    driver.get(url)
    try:
        WebDriverWait(driver, 30).until(
            lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 2000)
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
            lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 3000)
    except:
        pass
    time.sleep(5)

def escanear(driver, nombre, url, clicks_usados, inicio_tiempo):
    print(f"\n--- [B] {nombre} ---")
    resultados = []

    try:
        cargar_tabla(driver, url)
        filas = driver.find_elements(By.TAG_NAME, "tr")
        print(f"  [B] Total filas: {len(filas)}")

        for i, fila in enumerate(filas):
            minutos_pasados = (time.time() - inicio_tiempo) / 60
            if minutos_pasados > MAX_MINUTOS:
                print(f"  [B] Tope de tiempo alcanzado ({MAX_MINUTOS} min). Corto acá.")
                break
            if clicks_usados[0] >= MAX_CLICKS:
                print(f"  [B] Tope de clicks alcanzado ({MAX_CLICKS}). Corto acá.")
                break

            try:
                texto_fila = fila.text.strip()
            except Exception:
                continue
            if len(texto_fila) < 10 or es_fila_formulario(texto_fila):
                continue

            if detectar_productos(texto_fila):
                continue

            if not es_descripcion_generica(texto_fila):
                continue

            elem_ver_archivos = None
            try:
                for elem in fila.find_elements(By.XPATH, ".//*[@onclick]"):
                    onclick = elem.get_attribute("onclick") or ""
                    if "verArchivos" in onclick:
                        elem_ver_archivos = elem
                        break
            except Exception:
                continue
            if elem_ver_archivos is None:
                continue

            # Extraemos numero/ejercicio ANTES de leer el documento, para
            # poder priorizar el link que corresponde a ESTA compulsa
            # puntual (patron CAB_{numero}_{ejercicio}_...) en vez de
            # agarrar cualquier PDF que aparezca en la pagina.
            nro_previo, ejercicio_previo = "", ""
            m = re.search(r'(\d+)/(\d+)', texto_fila)
            if m:
                nro_previo = m.group(1)
                ejercicio_previo = "20" + m.group(2) if len(m.group(2)) == 2 else m.group(2)

            clicks_usados[0] += 1
            print(f"  [B] [{clicks_usados[0]}/{MAX_CLICKS}] Leyendo doc fila {i}: {texto_fila[:100]}")

            try:
                texto_doc = leer_documento_via_click(driver, elem_ver_archivos, nro_previo, ejercicio_previo)
            except Exception as e:
                print(f"  [B] error leyendo documento: {e}")
                texto_doc = ""

            print(f"  [B] texto obtenido: {len(texto_doc)} caracteres")
            if not texto_doc:
                print(f"  [B] sin texto, se descarta esta fila")
                continue

            texto_doc = re.sub(r'\s+', ' ', texto_doc).strip()
            productos = detectar_productos(texto_doc)
            if not productos:
                continue

            print(f"  [B] ✅ ENCONTRADO: {', '.join(productos)}")

            nro, ejercicio, expediente, cierre, ugl = "", "", "", "", ""
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

            resultados.append({
                "productos": productos,
                "numero": nro,
                "ejercicio": ejercicio,
                "expediente": expediente,
                "ugl": ugl,
                "cierre": cierre,
                "fila_texto": texto_fila[:200],
                "buscador": nombre,
            })

    except Exception as e:
        print(f"  [B] Error general en {nombre}: {e}")

    return resultados

def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"=== [PROCESO B] Monitor PAMI documentos genéricos — {fecha} ===")

    resultados_totales = []
    error_msg = ""
    driver = None
    clicks_usados = [0]
    inicio_tiempo = time.time()

    try:
        driver = iniciar_browser()
        for nombre, url in URLS_BUSCADOR:
            resultados_totales.extend(
                escanear(driver, nombre, url, clicks_usados, inicio_tiempo)
            )
    except Exception as e:
        error_msg = str(e)
        print(f"=== [PROCESO B] ERROR GENERAL: {e} ===")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print(f"\n=== [PROCESO B] Total encontrados: {len(resultados_totales)} ===")
    print(f"=== [PROCESO B] Clicks usados: {clicks_usados[0]}/{MAX_CLICKS} ===")

    try:
        enviar_email(resultados_totales, fecha, error_msg)
    except Exception as e:
        print(f"[PROCESO B] No se pudo enviar el email: {e}")


def enviar_email(resultados, fecha, error_msg):
    if resultados:
        filas_html = ""
        for r in resultados:
            tags = " ".join(f"🔍 {p}" for p in r["productos"])
            filas_html += f"""
            <div style="background:#f8fafb;border-left:5px solid #27ae60;border-radius:6px;padding:14px 18px;margin-bottom:12px">
              <div style="font-weight:600;color:#1e8449;margin-bottom:6px">{tags}</div>
              <div style="font-size:13px;color:#555;line-height:1.7">
                <b>Buscador:</b> {r['buscador']}<br>
                <b>Compulsa:</b> {r['numero']}/{r['ejercicio']}<br>
                <b>UGL:</b> {r['ugl']}<br>
                <b>Cierre:</b> {r['cierre']}<br>
                <b>Expediente:</b> {r['expediente']}<br>
                <b>Texto de la fila:</b> {r['fila_texto']}
              </div>
            </div>"""
        asunto = f"🔎 PAMI (docs genéricos) | {len(resultados)} encontrado(s) — {fecha}"
    else:
        filas_html = "<p style='color:#555'>No se encontró nada en las compulsas con descripción genérica revisadas hoy.</p>"
        asunto = f"ℹ️ PAMI (docs genéricos) | Sin novedades — {fecha}"

    aviso = ""
    if error_msg:
        aviso = f"""<div style="background:#fdedec;border-left:4px solid #e74c3c;padding:12px 18px;margin-bottom:16px;color:#943126;font-size:13px">
        ⚠️ El proceso tuvo un error y puede no haber revisado todo: {error_msg}</div>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="font-family:Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px">
    <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden">
      <div style="background:#6c3483;padding:20px 26px">
        <h1 style="margin:0;color:#fff;font-size:18px">🔎 Monitor PAMI — Proceso B (docs genéricos)</h1>
        <p style="margin:4px 0 0;color:#d7bde2;font-size:12px">{fecha} · Revisión secundaria, independiente del monitor diario</p>
      </div>
      <div style="padding:22px 26px">
        {aviso}
        {filas_html}
      </div>
    </div></body></html>"""

    msg = MIMEMultipart("mixed")
    msg["From"] = EMAIL_ORIGEN
    msg["To"] = EMAIL_DESTINO
    msg["Subject"] = asunto
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_ORIGEN, EMAIL_PASS)
        server.send_message(msg)
    print(f"[PROCESO B] Email enviado: {asunto}")


if __name__ == "__main__":
    main()
