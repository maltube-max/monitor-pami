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

# Todas las variantes de búsqueda — juntas, separadas, con y sin mayúsculas
# Google busca sin importar mayúsculas, así que alcanza con una variante por producto
BUSQUEDAS = [
    # Clip Mitral
    'site:prestadores.pami.org.ar "clip mitral"',
    'site:prestadores.pami.org.ar "mitraclip"',
    'site:prestadores.pami.org.ar "mitra clip"',

    # Lux Valve
    'site:prestadores.pami.org.ar "lux valve"',
    'site:prestadores.pami.org.ar "lux value"',

    # Válvula Tricuspídea
    'site:prestadores.pami.org.ar "valvula tricuspide"',
    'site:prestadores.pami.org.ar "tricuspidea"',
    'site:prestadores.pami.org.ar "tricuspide percutanea"',

    # Protector Cerebral Sentinel
    'site:prestadores.pami.org.ar "sentinel"',
    'site:prestadores.pami.org.ar "protector cerebral"',

    # Bioadaptador
    'site:prestadores.pami.org.ar "bioadaptador"',
    'site:prestadores.pami.org.ar "bio adaptador"',

    # Ken Valve
    'site:prestadores.pami.org.ar "ken valve"',
]

# Mapeo de palabras clave a nombre de producto
PRODUCTO_MAP = {
    "clip mitral":           "Clip Mitral",
    "mitraclip":             "Clip Mitral",
    "mitra clip":            "Clip Mitral",
    "lux valve":             "Lux Valve",
    "lux value":             "Lux Valve",
    "valvula tricuspide":    "Válvula Tricuspídea",
    "tricuspidea":           "Válvula Tricuspídea",
    "tricuspide percutanea": "Válvula Tricuspídea",
    "sentinel":              "Protector Cerebral Sentinel",
    "protector cerebral":    "Protector Cerebral Sentinel",
    "bioadaptador":          "Bioadaptador",
    "bio adaptador":         "Bioadaptador",
    "ken valve":             "Ken Valve",
}

# ── NORMALIZAR TEXTO ───────────────────────────────────────
def normalizar(texto):
    texto = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        texto = texto.replace(a, b)
    return texto

# ── BUSCAR EN GOOGLE ───────────────────────────────────────
def buscar_en_google(query):
    """Hace una búsqueda en Google y retorna los links encontrados."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "es-AR,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    url = f"https://www.google.com/search?q={requests.utils.quote(query)}&num=10&hl=es"
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"Google respondió {resp.status_code} para: {query}")
            return []

        # Extraer links de los resultados
        links = []
        # Buscar URLs de prestadores.pami.org.ar en el HTML
        patron = r'https?://prestadores\.pami\.org\.ar/[^\s"&<>]+'
        encontrados = re.findall(patron, resp.text)
        for link in encontrados:
            # Limpiar la URL
            link = link.split('"')[0].split("'")[0].split("\\")[0]
            if link not in links:
                links.append(link)
        
        print(f"  '{query}' → {len(links)} links encontrados")
        return links

    except Exception as e:
        print(f"  Error buscando '{query}': {e}")
        return []

# ── LEER PDF ───────────────────────────────────────────────
def leer_pdf(url):
    """Descarga un PDF y extrae su texto."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return None, None
        
        content_type = resp.headers.get("Content-Type", "")
        
        # Si es PDF, extraer texto
        if "pdf" in content_type.lower() or url.lower().endswith(".pdf"):
            # Usar pdfminer para extraer texto
            try:
                import io
                from pdfminer.high_level import extract_text
                texto = extract_text(io.BytesIO(resp.content))
                return texto, resp.content
            except:
                # Si no tiene pdfminer, buscar texto básico en el binario
                texto = resp.content.decode("latin-1", errors="ignore")
                return texto, resp.content
        
        # Si es HTML
        elif "html" in content_type.lower():
            from html.parser import HTMLParser
            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, data):
                    self.text.append(data)
            parser = TextExtractor()
            parser.feed(resp.text)
            return " ".join(parser.text), None
            
    except Exception as e:
        print(f"  Error leyendo {url}: {e}")
    return None, None

# ── ANALIZAR DOCUMENTO ─────────────────────────────────────
def analizar_documento(url, texto):
    """Busca todas las palabras clave en el texto de un documento."""
    texto_norm = normalizar(texto)
    productos_encontrados = set()
    
    for palabra, producto in PRODUCTO_MAP.items():
        if normalizar(palabra) in texto_norm:
            productos_encontrados.add(producto)
    
    return list(productos_encontrados)

# ── EXTRAER INFO DEL DOCUMENTO ─────────────────────────────
def extraer_info(texto):
    """Extrae número de compulsa, UGL, fecha de cierre del texto."""
    info = {}
    texto_norm = texto.upper()
    
    # Número de compulsa
    m = re.search(r'COMPULSA\s+(?:ABREVIADA\s+)?(?:N[°º]?:?\s*)?(\d+)', texto_norm)
    if m:
        info["numero"] = m.group(1)
    
    # UGL
    m = re.search(r'UGL[:\s]+(\d+\s*[-–]\s*[A-ZÁÉÍÓÚ\s]+)', texto_norm)
    if m:
        info["ugl"] = m.group(1).strip()[:50]
    
    # Fecha de cierre/apertura
    m = re.search(r'APERTURA[:\s]+(?:SE\s+RECIBIR[AÁ]N[^:]+HASTA\s+EL\s+D[IÍ]A\s+)?(\d{1,2}/\d{1,2}/\d{4})', texto_norm)
    if m:
        info["cierre"] = m.group(1)
    
    # Email de contacto
    m = re.search(r'[\w\.-]+@pami\.org\.ar', texto.lower())
    if m:
        info["email_contacto"] = m.group(0)

    return info

# ── MAIN ───────────────────────────────────────────────────
def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"=== Monitor PAMI — {fecha} ===")

    # 1. Buscar en Google todas las palabras clave
    links_encontrados = {}  # url -> set de palabras que la encontraron

    for query in BUSQUEDAS:
        links = buscar_en_google(query)
        # Extraer la palabra clave de la query
        m = re.search(r'"([^"]+)"', query)
        palabra = m.group(1) if m else query
        
        for link in links:
            if link not in links_encontrados:
                links_encontrados[link] = set()
            links_encontrados[link].add(palabra)
        
        time.sleep(2)  # Pausa para no saturar Google

    print(f"\nURLs únicas encontradas: {len(links_encontrados)}")

    if not links_encontrados:
        print("Google no devolvió resultados — enviando email sin coincidencias")
        enviar_email_sin_coincidencias(fecha)
        return

    # 2. Leer cada documento y confirmar palabras clave
    resultados = []
    urls_procesadas = set()

    for url, palabras_google in links_encontrados.items():
        if url in urls_procesadas:
            continue
        urls_procesadas.add(url)
        
        print(f"\nLeyendo: {url}")
        texto, contenido_pdf = leer_pdf(url)
        
        if not texto:
            print(f"  No se pudo leer el documento")
            continue
        
        # Buscar todas las palabras clave en el documento
        productos = analizar_documento(url, texto)
        
        if productos:
            info = extraer_info(texto)
            resultados.append({
                "url": url,
                "productos": productos,
                "numero": info.get("numero", ""),
                "ugl": info.get("ugl", ""),
                "cierre": info.get("cierre", ""),
                "email_contacto": info.get("email_contacto", ""),
                "pdf_contenido": contenido_pdf,
                "es_pdf": contenido_pdf is not None,
            })
            print(f"  ✅ Encontrado: {', '.join(productos)}")
        else:
            print(f"  Sin coincidencias en el documento")
        
        time.sleep(1)

    # 3. Enviar email
    print(f"\nResultados finales: {len(resultados)} documento(s) con coincidencias")
    
    if resultados:
        enviar_email_con_coincidencias(resultados, fecha)
    else:
        enviar_email_sin_coincidencias(fecha)

# ── EMAILS ─────────────────────────────────────────────────
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
  .info{font-size:12px;color:#777;margin:6px 0}
  .footer{background:#f4f6f9;padding:14px 30px;border-top:1px solid #e8ecf0;text-align:center}
  .footer p{margin:0;font-size:11px;color:#aaa}
  ul{padding-left:20px}
  ul li{margin:4px 0;color:#555;font-size:14px}
</style>
"""

def enviar_email_con_coincidencias(resultados, fecha):
    adjuntos = []
    cards = ""

    for i, r in enumerate(resultados):
        tags = "".join(f'<span class="tag">🔍 {p}</span>' for p in r["productos"])
        
        info_items = []
        if r.get("numero"):        info_items.append(f"<b>Compulsa N°:</b> {r['numero']}")
        if r.get("ugl"):           info_items.append(f"<b>UGL:</b> {r['ugl']}")
        if r.get("cierre"):        info_items.append(f"<b>Cierre:</b> {r['cierre']}")
        if r.get("email_contacto"):info_items.append(f"<b>Enviar a:</b> {r['email_contacto']}")
        info_str = "<br>".join(info_items)

        btn_doc = f'<a href="{r["url"]}" class="btn btn-blue">{"📄 Ver PDF" if r["es_pdf"] else "🔗 Ver documento"}</a>'

        cards += f"""
        <div class="card">
          <div style="margin-bottom:8px">{tags}</div>
          <p class="info">{info_str}</p>
          {btn_doc}
        </div>"""

        # Adjuntar PDF si existe
        if r.get("pdf_contenido") and len(adjuntos) < 5:
            nombre = f"pliego_{r.get('numero', str(i+1))}.pdf"
            adjuntos.append((nombre, r["pdf_contenido"]))

    adj_nota = f"<br><span style='font-size:13px;font-weight:normal'>📎 Se adjuntan {len(adjuntos)} PDF(s)</span>" if adjuntos else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header">
        <h1>🏥 Monitor PAMI — Siprotec</h1>
        <p>{fecha} | Revisión automática diaria</p>
      </div>
      <div class="banner-ok">
        <p>✅ Se encontraron <strong>{len(resultados)}</strong> documento(s) con productos Siprotec.{adj_nota}</p>
      </div>
      <div class="body">
        <h2 style="color:#1a5276;font-size:15px;margin:0 0 16px">Documentos detectados:</h2>
        {cards}
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔎 Ver portal PAMI</a>
        </div>
      </div>
      <div class="footer"><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""

    _enviar(f"✅ PAMI | {len(resultados)} pedido(s) encontrado(s) — {fecha}", html, adjuntos)

def enviar_email_sin_coincidencias(fecha):
    productos = ["Clip Mitral","Lux Valve","Válvula Tricuspídea","Protector Cerebral Sentinel","Bioadaptador","Ken Valve"]
    items = "".join(f"<li>{p}</li>" for p in productos)

    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">{ESTILO}</head><body>
    <div class="wrap">
      <div class="header">
        <h1>🏥 Monitor PAMI — Siprotec</h1>
        <p>{fecha} | Revisión automática diaria</p>
      </div>
      <div class="banner-no">
        <p>ℹ️ No se encontraron pedidos relevantes hoy.</p>
      </div>
      <div class="body">
        <p style="color:#555;font-size:14px">Productos monitoreados sin resultados:</p>
        <ul>{items}</ul>
        <div style="margin-top:20px;text-align:center">
          <a href="https://prestadores.pami.org.ar/result.php?c=7-5&par=2" class="btn btn-gray">🔗 Ver portal PAMI</a>
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
    print(f"✅ Email enviado a {EMAIL_DESTINO}: {asunto}")

if __name__ == "__main__":
    main()
