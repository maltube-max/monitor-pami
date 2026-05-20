import requests
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

# ── CONFIGURACIÓN ──────────────────────────────────────────
EMAIL_ORIGEN  = "marialtube@gmail.com"
EMAIL_PASS    = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_DESTINO = "cotizaciones@siprotec.com.ar"
URL_BASE      = "https://prestadores.pami.org.ar/"

# Los dos buscadores
URLS_BUSCADOR = [
    "https://prestadores.pami.org.ar/result.php?c=7-5&par=2",  # UGL
    "https://prestadores.pami.org.ar/result.php?c=7-5&par=1",  # Nivel Central
]

# Palabras clave por producto
PALABRAS_CLAVE = {
    "Clip Mitral":                 ["clip mitral", "mitraclip", "mitra clip"],
    "Lux Valve":                   ["lux valve", "lux value"],
    "Válvula Tricuspídea":         ["tricuspide", "tricuspidea", "valvula tricuspide"],
    "Protector Cerebral Sentinel": ["sentinel", "protector cerebral"],
    "Bioadaptador":                ["bioadaptador", "bio adaptador"],
    "Ken Valve":                   ["ken valve"],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}

# ── NORMALIZAR ─────────────────────────────────────────────
def normalizar(texto):
    texto = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        texto = texto.replace(a, b)
    return texto

# ── BUSCAR PALABRAS CLAVE EN TEXTO ─────────────────────────
def detectar_productos(texto):
    texto_norm = normalizar(texto)
    encontrados = []
    for producto, variantes in PALABRAS_CLAVE.items():
        for v in variantes:
            if normalizar(v) in texto_norm:
                encontrados.append(producto)
                break
    return encontrados

# ── OBTENER TODAS LAS COMPRAS EN CURSO ────────────────────
def obtener_todas_compras(url_buscador):
    """
    Hace POST sin descripcion para traer TODAS las compras en curso,
    luego extrae todos los links a documentos/PDFs.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        # GET primero para obtener cookies
        session.get(url_buscador, timeout=15)
        time.sleep(1)

        # POST sin descripcion = traer todo
        payload = {
            "descripcion": "",
            "fecha_desde": "",
            "fecha_hasta": "",
            "tipo_compra": "",
            "nro_compra":  "",
            "estado":      "En curso",
            "buscar":      "Buscar",
        }
        r = session.post(url_buscador, data=payload, timeout=30)

        if r.status_code != 200:
            print(f"  Error HTTP {r.status_code}")
            return [], session

        html = r.text
        print(f"  HTML recibido: {len(html)} chars")

        # Extraer todos los links del HTML
        links = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
        compras = []
        for link in links:
            if not link or link.startswith("javascript") or link == "#":
                continue
            if not link.startswith("http"):
                link = URL_BASE + link.lstrip("/")
            # Solo links que parezcan documentos de compras
            if any(x in link.lower() for x in [
                "pliego", "compra", "compulsa", "licitacion", "download",
                "ver_archivo", "archivo", "adjunto", ".pdf", "doc"
            ]):
                if link not in [c["link"] for c in compras]:
                    compras.append({"link": link, "es_pdf": ".pdf" in link.lower()})

        # También guardar el HTML completo como un item para buscar en él
        compras.insert(0, {
            "link": url_buscador,
            "es_pdf": False,
            "html_directo": html,
        })

        print(f"  Links de documentos encontrados: {len(compras)-1}")
        return compras, session

    except Exception as e:
        print(f"  Error: {e}")
        return [], session

# ── LEER DOCUMENTO ─────────────────────────────────────────
def leer_documento(link, session):
    """Lee un documento (PDF o HTML) y retorna su texto y bytes."""
    try:
        r = session.get(link, timeout=20)
        if r.status_code != 200:
            return None, None

        content_type = r.headers.get("Content-Type", "")

        if "pdf" in content_type.lower() or link.lower().endswith(".pdf"):
            try:
                from pdfminer.high_level import extract_text as pdf_extract
                texto = pdf_extract(io.BytesIO(r.content))
                return texto, r.content
            except:
                return r.content.decode("latin-1", errors="ignore"), r.content

        elif "html" in content_type.lower():
            texto = re.sub(r'<[^>]+>', ' ', r.text)
            texto = re.sub(r'\s+', ' ', texto).strip()
            return texto, None

    except Exception as e:
        print(f"    Error leyendo {link}: {e}")
    return None, None

# ── EXTRAER INFO DEL DOCUMENTO ─────────────────────────────
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

# ── MAIN ───────────────────────────────────────────────────
def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"=== Monitor PAMI — {fecha} ===")

    resultados = {}
    adjuntos = []

    for url_buscador in URLS_BUSCADOR:
        nombre = "UGL" if "par=2" in url_buscador else "Nivel Central"
        print(f"\n--- {nombre} ---")

        compras, session = obtener_todas_compras(url_buscador)

        for compra in compras:
            link = compra["link"]

            # Si el HTML ya vino en la respuesta directa, usarlo
            if compra.get("html_directo"):
                texto = re.sub(r'<[^>]+>', ' ', compra["html_directo"])
                texto = re.sub(r'\s+', ' ', texto).strip()
                bytes_doc = None
            else:
                print(f"  Revisando: {link}")
                texto, bytes_doc = leer_documento(link, session)
                time.sleep(0.5)

            if not texto:
                continue

            productos = detectar_productos(texto)
            if productos:
                if link not in resultados:
                    info = extraer_info(texto)
                    resultados[link] = {
                        "link":     link,
                        "es_pdf":   compra["es_pdf"],
                        "productos": productos,
                        "info":     info,
                        "buscador": nombre,
                    }
                    print(f"  ✅ ENCONTRADO: {', '.join(productos)} — {link}")
                    if bytes_doc and len(adjuntos) < 5:
                        nombre_pdf = f"pliego_{info.get('numero', len(adjuntos)+1)}.pdf"
                        adjuntos.append((nombre_pdf, bytes_doc))

    print(f"\n=== Total: {len(resultados)} compra(s) encontrada(s) ===")

    if resultados:
        enviar_email_con_coincidencias(list(resultados.values()), adjuntos, fecha)
    else:
        enviar_email_sin_coincidencias(fecha)

# ── EMAILS ─────────────────────────────────────────────────
ESTILO = """<style>
body{font-family:'Helvetica Neue',Arial,sans-serif;background:#f4f6f9;margin:0;padding:0}
.wrap{max-width:640px;margin:30px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)}
.header{background:#1a5276;padding:22px 30px}
.header h1{margin:0;color:#fff;font-size:20px}
.header p{margin:5px 0 0;color:#aed6f1;font-size:13px}
.banner-ok{background:#eafaf1;border-left:4px solid #27ae60;padding:14px 24px}
.banner-ok p{margin:0;color:#1e8449;font-size:15px;font-weight:600}
.banner-no{background:#fdfefe;border-left:4px solid #85929e;padding:14px 24px}
.banner-no p{margin:0;color:#5d6d7e;font-size:15px;font-weight:600}
.body{padding:24px 30px}
.card{background:#f8fafb;border:1px solid #d5e8d4;border-left:5px solid #27ae60;border-radius:6px;padding:16px 20px;margin-bottom:16px}
.tag{display:inline-block;background:#eafaf1;color:#1e8449;padding:2px 10px;border-radius:4px;font-size:13px;margin:2px;font-weight:600}
.btn{display:inline-block;padding:10px 22px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600;margin-top:10px;margin-right:8px}
.btn-blue{background:#1a5276;color:#fff!important}
.btn-gray{background:#85929e;color:#fff!important}
.info{font-size:13px;color:#555;margin:8px 0;line-height:1.8}
.footer{background:#f4f6f9;padding:14px 30px;border-top:1px solid #e8ecf0;text-align:center}
.footer p{margin:0;font-size:11px;color:#aaa}
ul{padding-left:20px}
ul li{margin:4px 0;color:#555;font-size:14px}
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
        cards += f"""<div class="card">
          <div style="margin-bottom:10px">{tags}</div>
          <p class="info">{info_html}</p>
          {btn}</div>"""

    adj_nota = f"<br><span style='font-size:13px;font-weight:normal'>📎 {len(adjuntos)} pliego(s) PDF adjunto(s)</span>" if adjuntos else ""
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header"><h1>🏥 Monitor PAMI — Siprotec</h1><p>{fecha} | Revisión automática diaria</p></div>
      <div class="banner-ok"><p>✅ <strong>{len(resultados)}</strong> compra(s) encontrada(s) con productos Siprotec.{adj_nota}</p></div>
      <div class="body">
        <h2 style="color:#1a5276;font-size:15px;margin:0 0 16px">Compras detectadas:</h2>
        {cards}
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔎 PAMI UGL</a>
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=1" class="btn btn-gray">🔎 PAMI Central</a>
        </div>
      </div>
      <div class="footer"><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""
    _enviar(f"✅ PAMI | {len(resultados)} pedido(s) encontrado(s) — {fecha}", html, adjuntos)

def enviar_email_sin_coincidencias(fecha):
    items = "".join(f"<li>{p}</li>" for p in PALABRAS_CLAVE.keys())
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header"><h1>🏥 Monitor PAMI — Siprotec</h1><p>{fecha} | Revisión automática diaria</p></div>
      <div class="banner-no"><p>ℹ️ No se encontraron pedidos relevantes hoy en UGL ni Nivel Central.</p></div>
      <div class="body">
        <p style="color:#555;font-size:14px">Productos monitoreados:</p>
        <ul>{items}</ul>
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔗 PAMI UGL</a>
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=1" class="btn btn-gray">🔗 PAMI Central</a>
        </div>
      </div>
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
