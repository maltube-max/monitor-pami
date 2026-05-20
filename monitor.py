import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import os
import re

# ── CONFIGURACIÓN ──────────────────────────────────────────
URL_PORTAL = "https://prestadores.pami.org.ar/result.php?c=7-5&par=2"
URL_BASE   = "https://prestadores.pami.org.ar/"

EMAIL_ORIGEN  = "marialtube@gmail.com"
EMAIL_PASS    = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_DESTINO = "cotizaciones@siprotec.com.ar"

PALABRAS_CLAVE = {
    "Clip Mitral":                ["clip mitral", "clip-mitral", "clipmitral", "mitraclip", "mitra clip"],
    "Lux Valve":                  ["lux valve", "lux-valve", "luxvalve", "lux value"],
    "Válvula Tricuspídea":        ["valvula tricuspidea", "valvula tricuspide", "tricuspidea",
                                   "tricuspide", "tricuspid", "valvula tricuspídea", "válvula tricúspide"],
    "Protector Cerebral Sentinel":["sentinel", "protector cerebral", "filtro cerebral"],
    "Bioadaptador":               ["bioadaptador", "bio adaptador", "bio-adaptador"],
    "Ken Valve":                  ["ken valve", "ken-valve", "kenvalve"],
}

# ── NORMALIZAR TEXTO ───────────────────────────────────────
def normalizar(texto):
    texto = texto.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        texto = texto.replace(a, b)
    return texto

# ── OBTENER DATOS DE PAMI ──────────────────────────────────
def obtener_compras():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9",
    }

    compras = []

    # Intentar endpoints AJAX conocidos
    endpoints = [
        ("POST", "https://prestadores.pami.org.ar/compras_ugl_ajax.php", {"estado":"En curso"}),
        ("POST", "https://prestadores.pami.org.ar/ajax/get_compras.php", {"estado":"En curso"}),
        ("GET",  "https://prestadores.pami.org.ar/compras_ugl_json.php", {}),
        ("GET",  "https://prestadores.pami.org.ar/get_compras_ugl.php",  {}),
    ]

    for metodo, url, payload in endpoints:
        try:
            if metodo == "POST":
                r = requests.post(url, data=payload, headers=headers, timeout=15)
            else:
                r = requests.get(url, headers=headers, timeout=15)

            if r.status_code == 200 and len(r.text) > 200 and "Cargando" not in r.text:
                # Intentar parsear como JSON
                try:
                    data = r.json()
                    items = data if isinstance(data, list) else data.get("data", data.get("items", []))
                    for item in items:
                        texto = normalizar(str(item))
                        compras.append({
                            "texto": texto,
                            "titulo": str(item.get("descripcion", item.get("objeto", ""))[:200]),
                            "link": item.get("url", item.get("link", URL_PORTAL)),
                            "pdf": item.get("pdf", item.get("pliego", item.get("archivo", ""))),
                            "numero": str(item.get("numero", item.get("compulsa", item.get("nro", "")))),
                            "ugl": str(item.get("ugl", item.get("destino", ""))),
                            "cierre": str(item.get("cierre", item.get("apertura", ""))),
                        })
                    if compras:
                        print(f"✅ Datos obtenidos de {url} ({len(compras)} compras)")
                        return compras
                except:
                    # Parsear como HTML
                    soup = BeautifulSoup(r.text, "html.parser")
                    filas = soup.find_all("tr")
                    for fila in filas:
                        texto = normalizar(fila.get_text(" ", strip=True))
                        if len(texto) < 20:
                            continue
                        link_tag = fila.find("a", href=True)
                        href = link_tag["href"] if link_tag else ""
                        if href and not href.startswith("http"):
                            href = URL_BASE + href.lstrip("/")
                        es_pdf = href.lower().endswith(".pdf")
                        compras.append({
                            "texto": texto,
                            "titulo": texto[:200],
                            "link": href if href else URL_PORTAL,
                            "pdf": href if es_pdf else "",
                            "numero": "",
                            "ugl": "",
                            "cierre": "",
                        })
                    if compras:
                        print(f"✅ HTML parseado de {url} ({len(compras)} filas)")
                        return compras
        except Exception as e:
            print(f"⚠️  {url}: {e}")

    # Último recurso: página principal con requests-html simulation
    try:
        r = requests.get(URL_PORTAL, headers=headers, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            texto_completo = normalizar(soup.get_text(" ", strip=True))

            # Buscar todos los links a PDFs en la página
            pdfs = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if ".pdf" in href.lower() or "pliego" in href.lower() or "compulsa" in href.lower():
                    if not href.startswith("http"):
                        href = URL_BASE + href.lstrip("/")
                    pdfs.append(href)

            compras.append({
                "texto": texto_completo,
                "titulo": "Contenido completo de la página PAMI",
                "link": URL_PORTAL,
                "pdf": pdfs[0] if pdfs else "",
                "pdfs_extra": pdfs,
                "numero": "",
                "ugl": "",
                "cierre": "",
            })
            print(f"✅ Página principal obtenida ({len(texto_completo)} chars, {len(pdfs)} PDFs encontrados)")
            return compras
    except Exception as e:
        print(f"❌ Error en página principal: {e}")

    return []

# ── BUSCAR PALABRAS CLAVE ──────────────────────────────────
def buscar_coincidencias(compras):
    resultados = []
    for compra in compras:
        texto = compra["texto"]
        encontrados = []
        for producto, variantes in PALABRAS_CLAVE.items():
            for v in variantes:
                if normalizar(v) in texto:
                    encontrados.append(producto)
                    break
        if encontrados:
            compra["productos"] = encontrados
            resultados.append(compra)
    return resultados

# ── DESCARGAR PDF ──────────────────────────────────────────
def descargar_pdf(url):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and "pdf" in r.headers.get("Content-Type","").lower():
            return r.content
    except:
        pass
    return None

# ── ENVIAR EMAIL ───────────────────────────────────────────
def enviar_email(asunto, cuerpo_html, adjuntos=None):
    msg = MIMEMultipart("mixed")
    msg["From"]    = EMAIL_ORIGEN
    msg["To"]      = EMAIL_DESTINO
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))

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
    print(f"✅ Email enviado a {EMAIL_DESTINO}")

# ── TEMPLATES DE EMAIL ─────────────────────────────────────
ESTILO = """
<style>
  body { font-family: 'Helvetica Neue', Arial, sans-serif; background:#f4f6f9; margin:0; padding:0; }
  .wrap { max-width:640px; margin:30px auto; background:#fff; border-radius:8px;
          overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.1); }
  .header { background:#1a5276; padding:22px 30px; }
  .header h1 { margin:0; color:#fff; font-size:20px; }
  .header p  { margin:5px 0 0; color:#aed6f1; font-size:13px; }
  .banner-ok  { background:#eafaf1; border-left:4px solid #27ae60; padding:14px 24px; }
  .banner-ok p { margin:0; color:#1e8449; font-size:15px; font-weight:600; }
  .banner-no  { background:#fdfefe; border-left:4px solid #85929e; padding:14px 24px; }
  .banner-no p { margin:0; color:#5d6d7e; font-size:15px; font-weight:600; }
  .body { padding:24px 30px; }
  .card { background:#f8fafb; border:1px solid #d5e8d4; border-left:5px solid #27ae60;
          border-radius:6px; padding:16px 20px; margin-bottom:16px; }
  .tag  { display:inline-block; background:#eafaf1; color:#1e8449; padding:2px 10px;
          border-radius:4px; font-size:13px; margin:2px; font-weight:600; }
  .btn  { display:inline-block; padding:10px 22px; border-radius:6px; text-decoration:none;
          font-size:14px; font-weight:600; margin-top:10px; margin-right:8px; }
  .btn-blue { background:#1a5276; color:#fff !important; }
  .btn-gray { background:#85929e; color:#fff !important; }
  .info { font-size:12px; color:#777; margin:6px 0; font-style:italic; }
  .footer { background:#f4f6f9; padding:14px 30px; border-top:1px solid #e8ecf0; text-align:center; }
  .footer p { margin:0; font-size:11px; color:#aaa; }
  ul { padding-left:20px; }
  ul li { margin:4px 0; color:#555; font-size:14px; }
</style>
"""

def email_con_coincidencias(coincidencias, fecha, adjuntos):
    cards = ""
    for c in coincidencias:
        tags = "".join(f'<span class="tag">🔍 {p}</span>' for p in c["productos"])
        info = []
        if c.get("ugl"):    info.append(f"<b>UGL:</b> {c['ugl']}")
        if c.get("numero"): info.append(f"<b>N°:</b> {c['numero']}")
        if c.get("cierre"): info.append(f"<b>Cierre:</b> {c['cierre']}")
        info_str = " &nbsp;|&nbsp; ".join(info) if info else ""

        btn_pdf  = f'<a href="{c["pdf"]}" class="btn btn-blue">📄 Descargar PDF</a>' if c.get("pdf") else ""
        btn_link = f'<a href="{c["link"]}" class="btn btn-gray">🔗 Ver en PAMI</a>' if c.get("link") else ""

        cards += f"""
        <div class="card">
          <div>{tags}</div>
          {f'<p class="info">{info_str}</p>' if info_str else ''}
          <p class="info">{c['titulo'][:200]}</p>
          {btn_pdf}{btn_link}
        </div>"""

    adj_nota = f"<br><span style='font-size:13px;font-weight:normal;'>📎 Se adjuntan {len(adjuntos)} pliego(s) PDF.</span>" if adjuntos else ""

    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'>{ESTILO}</head><body>
    <div class='wrap'>
      <div class='header'>
        <h1>🏥 Monitor PAMI — Siprotec</h1>
        <p>{fecha} | Revisión automática diaria</p>
      </div>
      <div class='banner-ok'>
        <p>✅ Se encontraron <strong>{len(coincidencias)}</strong> compra(s) con productos Siprotec.{adj_nota}</p>
      </div>
      <div class='body'>
        <h2 style='color:#1a5276;font-size:15px;margin:0 0 16px;'>Compras detectadas:</h2>
        {cards}
        <div style='margin-top:20px;text-align:center;'>
          <a href='{URL_PORTAL}' class='btn btn-gray'>🔎 Ver todos los pedidos en PAMI</a>
        </div>
      </div>
      <div class='footer'><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""

def email_sin_coincidencias(total, fecha):
    items = "".join(f"<li>{p}</li>" for p in PALABRAS_CLAVE.keys())
    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'>{ESTILO}</head><body>
    <div class='wrap'>
      <div class='header'>
        <h1>🏥 Monitor PAMI — Siprotec</h1>
        <p>{fecha} | Revisión automática diaria</p>
      </div>
      <div class='banner-no'>
        <p>ℹ️ No hay pedidos relevantes hoy.
        <br><span style='font-size:13px;font-weight:normal;'>Se revisaron <strong>{total}</strong> compra(s) vigente(s).</span></p>
      </div>
      <div class='body'>
        <p style='color:#555;font-size:14px;'>Productos monitoreados sin resultados:</p>
        <ul>{items}</ul>
        <div style='margin-top:20px;text-align:center;'>
          <a href='{URL_PORTAL}' class='btn btn-gray'>🔗 Ver portal PAMI</a>
        </div>
      </div>
      <div class='footer'><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""

def email_sin_datos(fecha):
    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'>{ESTILO}</head><body>
    <div class='wrap'>
      <div class='header'>
        <h1>🏥 Monitor PAMI — Siprotec</h1>
        <p>{fecha} | Revisión automática diaria</p>
      </div>
      <div style='background:#fef9e7;border-left:4px solid #e67e22;padding:14px 24px;'>
        <p style='margin:0;color:#9a5500;font-size:15px;font-weight:600;'>
          ⚠️ No se pudieron obtener datos del portal PAMI hoy.
        </p>
      </div>
      <div class='body'>
        <p style='color:#555;font-size:14px;'>
          El portal carga sus datos dinámicamente. Por favor revisá manualmente:
        </p>
        <div style='text-align:center;margin-top:16px;'>
          <a href='{URL_PORTAL}' class='btn btn-blue'>🔗 Ir al portal PAMI</a>
        </div>
      </div>
      <div class='footer'><p>Monitor automático PAMI · Siprotec S.A. · Revisión diaria 7 AM (ARG)</p></div>
    </div></body></html>"""

# ── MAIN ───────────────────────────────────────────────────
def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    print(f"=== Monitor PAMI — {fecha} ===")

    compras = obtener_compras()

    if not compras:
        print("❌ No se pudieron obtener datos")
        enviar_email(
            f"⚠️ PAMI | Sin datos — {fecha}",
            email_sin_datos(fecha)
        )
        return

    coincidencias = buscar_coincidencias(compras)
    print(f"Compras revisadas: {len(compras)} | Coincidencias: {len(coincidencias)}")

    if coincidencias:
        # Intentar descargar PDFs
        adjuntos = []
        for c in coincidencias:
            if c.get("pdf"):
                contenido = descargar_pdf(c["pdf"])
                if contenido:
                    nombre = f"pliego_{c.get('numero','pami')}.pdf"
                    adjuntos.append((nombre, contenido))
                    print(f"📎 PDF descargado: {nombre}")

        enviar_email(
            f"✅ PAMI | {len(coincidencias)} pedido(s) encontrado(s) — {fecha}",
            email_con_coincidencias(coincidencias, fecha, adjuntos),
            adjuntos if adjuntos else None
        )
    else:
        enviar_email(
            f"ℹ️ PAMI | Sin pedidos relevantes — {fecha}",
            email_sin_coincidencias(len(compras), fecha)
        )

if __name__ == "__main__":
    main()
