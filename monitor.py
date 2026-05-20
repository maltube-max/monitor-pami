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

# ── CONFIGURACIÓN ──────────────────────────────────────────
EMAIL_ORIGEN  = "marialtube@gmail.com"
EMAIL_PASS    = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_DESTINO = "cotizaciones@siprotec.com.ar"

URL_BASE = "https://prestadores.pami.org.ar/"

# Los dos buscadores: UGL y Nivel Central
URLS_BUSCADOR = [
    "https://prestadores.pami.org.ar/result.php?c=7-5&par=2",  # UGL
    "https://prestadores.pami.org.ar/result.php?c=7-5&par=1",  # Nivel Central
]

# Palabras clave por producto — se busca cada variante por separado en el formulario
BUSQUEDAS = {
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

# ── BUSCAR EN FORMULARIO ───────────────────────────────────
def buscar_en_formulario(url_buscador, descripcion):
    """Envía el formulario con una descripción y retorna el HTML del resultado."""
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.get(url_buscador, timeout=15)  # obtener cookies

        payload = {
            "descripcion": descripcion,
            "fecha_desde": "",
            "fecha_hasta": "",
            "tipo_compra": "",
            "nro_compra":  "",
            "estado":      "En curso",
            "buscar":      "Buscar",
        }
        r = session.post(url_buscador, data=payload, timeout=20)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"  Error buscando '{descripcion}': {e}")
    return None

# ── PARSEAR RESULTADOS ─────────────────────────────────────
def parsear_resultados(html, url_buscador):
    """Extrae filas de resultados del HTML y retorna lista de compras."""
    if not html or "Cargando" in html:
        return []

    compras = []
    
    # Buscar filas de tabla con resultados
    filas = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
    
    for fila in filas:
        texto = re.sub(r'<[^>]+>', ' ', fila)
        texto = re.sub(r'\s+', ' ', texto).strip()
        
        if len(texto) < 30:
            continue
        
        # Saltar filas de encabezado
        if normalizar(texto).startswith("descripcion") or normalizar(texto).startswith("n°"):
            continue

        # Buscar links a PDFs o documentos
        links = re.findall(r'href=["\']([^"\']+)["\']', fila, re.IGNORECASE)
        link_doc = ""
        link_pdf = ""
        for l in links:
            if not l.startswith("http"):
                l = URL_BASE + l.lstrip("/")
            if ".pdf" in l.lower():
                link_pdf = l
            elif any(x in l.lower() for x in ["pliego","compra","download","ver","detalle"]):
                link_doc = l

        if texto:
            compras.append({
                "texto":     normalizar(texto),
                "titulo":    texto[:300],
                "link":      link_pdf or link_doc or url_buscador,
                "link_pdf":  link_pdf,
                "es_pdf":    bool(link_pdf),
            })

    return compras

# ── DESCARGAR Y LEER PDF ───────────────────────────────────
def leer_pdf(url):
    """Descarga un PDF y extrae su texto. Retorna (texto, bytes)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None, None
        content_type = r.headers.get("Content-Type","")
        if "pdf" in content_type.lower() or url.lower().endswith(".pdf"):
            try:
                import io
                from pdfminer.high_level import extract_text
                texto = extract_text(io.BytesIO(r.content))
                return texto, r.content
            except:
                return r.content.decode("latin-1", errors="ignore"), r.content
    except Exception as e:
        print(f"  Error leyendo PDF {url}: {e}")
    return None, None

# ── EXTRAER INFO ───────────────────────────────────────────
def extraer_info(texto):
    info = {}
    t = texto.upper()
    m = re.search(r'COMPULSA[^\d]*(\d+)', t)
    if m: info["numero"] = m.group(1)
    m = re.search(r'UGL[:\s#]+(\d+\s*[-–]\s*[\w\s]+)', t)
    if m: info["ugl"] = m.group(1).strip()[:50]
    m = re.search(r'(\d{2}/\d{2}/\d{4})\s*[-–]\s*8:00', t)
    if m: info["cierre"] = m.group(1)
    m = re.search(r'[\w\.\-]+@pami\.org\.ar', texto.lower())
    if m: info["email_contacto"] = m.group(0)
    return info

# ── MAIN ───────────────────────────────────────────────────
def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"=== Monitor PAMI — {fecha} ===")

    resultados = {}  # link -> dict con info del resultado

    for url_buscador in URLS_BUSCADOR:
        nombre_buscador = "UGL" if "par=2" in url_buscador else "Nivel Central"
        print(f"\n--- Buscando en {nombre_buscador} ---")

        for producto, variantes in BUSQUEDAS.items():
            for variante in variantes:
                print(f"  Buscando: '{variante}'")
                html = buscar_en_formulario(url_buscador, variante)
                compras = parsear_resultados(html, url_buscador)

                for compra in compras:
                    link = compra["link"]
                    if link not in resultados:
                        resultados[link] = {
                            **compra,
                            "productos":  set(),
                            "buscador":   nombre_buscador,
                            "pdf_bytes":  None,
                            "info":       {},
                        }
                    resultados[link]["productos"].add(producto)

                time.sleep(1.5)

    print(f"\nTotal coincidencias únicas: {len(resultados)}")

    # Intentar descargar PDFs y extraer info
    adjuntos = []
    for link, datos in resultados.items():
        if datos["es_pdf"]:
            print(f"Descargando PDF: {link}")
            texto_pdf, bytes_pdf = leer_pdf(link)
            if bytes_pdf:
                datos["pdf_bytes"] = bytes_pdf
                datos["info"] = extraer_info(texto_pdf or "")
                adjuntos.append((
                    f"pliego_{datos['info'].get('numero', len(adjuntos)+1)}.pdf",
                    bytes_pdf
                ))

    # Convertir sets a listas
    for datos in resultados.values():
        datos["productos"] = list(datos["productos"])

    if resultados:
        enviar_email_con_coincidencias(list(resultados.values()), adjuntos, fecha)
    else:
        enviar_email_sin_coincidencias(fecha)

# ── TEMPLATES EMAIL ────────────────────────────────────────
ESTILO = """
<style>
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
  .info{font-size:13px;color:#555;margin:8px 0;line-height:1.6}
  .footer{background:#f4f6f9;padding:14px 30px;border-top:1px solid #e8ecf0;text-align:center}
  .footer p{margin:0;font-size:11px;color:#aaa}
  ul{padding-left:20px}
  ul li{margin:4px 0;color:#555;font-size:14px}
</style>
"""

def enviar_email_con_coincidencias(resultados, adjuntos, fecha):
    cards = ""
    for r in resultados:
        tags = "".join(f'<span class="tag">🔍 {p}</span>' for p in r["productos"])
        info = r.get("info", {})
        info_items = []
        if info.get("numero"):        info_items.append(f"<b>Compulsa N°:</b> {info['numero']}")
        if info.get("ugl"):           info_items.append(f"<b>UGL:</b> {info['ugl']}")
        if info.get("cierre"):        info_items.append(f"<b>⚠️ Cierre:</b> {info['cierre']}")
        if info.get("email_contacto"):info_items.append(f"<b>Enviar cotización a:</b> {info['email_contacto']}")
        if r.get("buscador"):         info_items.append(f"<b>Sección:</b> {r['buscador']}")
        info_str = "<br>".join(info_items)

        btn = f'<a href="{r["link"]}" class="btn btn-blue">{"📄 Ver PDF" if r["es_pdf"] else "🔗 Ver compra"}</a>'

        cards += f"""
        <div class="card">
          <div style="margin-bottom:10px">{tags}</div>
          <p class="info">{info_str}</p>
          <p style="font-size:12px;color:#888;margin:6px 0">{r['titulo'][:250]}</p>
          {btn}
        </div>"""

    adj_nota = f"<br><span style='font-size:13px;font-weight:normal'>📎 Se adjuntan {len(adjuntos)} pliego(s) PDF</span>" if adjuntos else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header">
        <h1>🏥 Monitor PAMI — Siprotec</h1>
        <p>{fecha} | Revisión automática diaria</p>
      </div>
      <div class="banner-ok">
        <p>✅ Se encontraron <strong>{len(resultados)}</strong> compra(s) con productos Siprotec.{adj_nota}</p>
      </div>
      <div class="body">
        <h2 style="color:#1a5276;font-size:15px;margin:0 0 16px">Compras detectadas:</h2>
        {cards}
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔎 Ver portal PAMI UGL</a>
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=1" class="btn btn-gray">🔎 Ver portal PAMI Central</a>
        </div>
      </div>
      <div class="footer"><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""

    _enviar(f"✅ PAMI | {len(resultados)} pedido(s) encontrado(s) — {fecha}", html, adjuntos)

def enviar_email_sin_coincidencias(fecha):
    productos = list(BUSQUEDAS.keys())
    items = "".join(f"<li>{p}</li>" for p in productos)
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header">
        <h1>🏥 Monitor PAMI — Siprotec</h1>
        <p>{fecha} | Revisión automática diaria</p>
      </div>
      <div class="banner-no">
        <p>ℹ️ No se encontraron pedidos relevantes hoy en UGL ni Nivel Central.</p>
      </div>
      <div class="body">
        <p style="color:#555;font-size:14px">Productos monitoreados sin resultados:</p>
        <ul>{items}</ul>
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔗 Ver portal PAMI UGL</a>
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=1" class="btn btn-gray">🔗 Ver portal PAMI Central</a>
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
