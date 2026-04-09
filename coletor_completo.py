"""
=============================================================
COLETOR ETA TAPACURA - V2 (CALIBRADO)
=============================================================
Extrai TODOS os pontos dos graficos do PI Vision via SVG.
Calibrado com as cores reais dos graficos da COMPESA:
  - Laranja rgb(224,138,0) = Cloro Residual (ppm)
  - Amarelo rgb(255,240,0) = Cor (uC)
  - Verde   rgb(60,191,60) = Turbidez (NTU)
  - Roxo    rgb(178,107,255) = pH
=============================================================
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    log.error("Rode: pip install playwright && playwright install chromium")
    sys.exit(1)

# ============================================================
# CONFIGURACOES
# ============================================================
URL = "https://supervisorio.compesa.com.br/PIVision/#/Displays/704/ETA-TAPACURA--INSTRUMENTACAO-ANALITICA?hidetoolbar&hidesidebar"
USUARIO = os.environ.get("ETA_USUARIO", "SEU_USUARIO_AQUI")
SENHA = os.environ.get("ETA_SENHA", "SUA_SENHA_AQUI")

ARQUIVO_DADOS = "dados/dados_eta.json"
ARQUIVO_SCREENSHOT = "dados/ultimo_screenshot.png"
ARQUIVO_DEBUG = "dados/debug_html.txt"

# Mapeamento de cores SVG para parametros
# Baseado nos dados reais coletados do PI Vision
CORES_PARAMETROS = {
    "rgb(224, 138, 0)":   "cloro_ppm",
    "rgb(224,138,0)":     "cloro_ppm",
    "rgb(255, 240, 0)":   "cor_uc",
    "rgb(255,240,0)":     "cor_uc",
    "rgb(60, 191, 60)":   "turbidez_ntu",
    "rgb(60,191,60)":     "turbidez_ntu",
    "rgb(178, 107, 255)": "ph",
    "rgb(178,107,255)":   "ph",
}

# Escalas dos eixos Y (baseado na imagem do PI Vision)
ESCALAS = {
    "cloro_ppm":     {"min": 0, "max": 6},
    "cor_uc":        {"min": 0, "max": 250},
    "turbidez_ntu":  {"min": 0, "max": 20},
    "ph":            {"min": 0, "max": 7},
}
# ============================================================


JS_EXTRAIR_GRAFICOS = """
() => {
    const resultado = {
        graficos: [],
        eixos_y: [],
        eixos_x: [],
        valores_texto: [],
        debug: []
    };

    const svgs = document.querySelectorAll('svg');
    resultado.debug.push('SVGs: ' + svgs.length);

    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        if (bbox.width < 100 || bbox.height < 50) return;  // ignorar SVGs pequenos

        const paths = svg.querySelectorAll('path.trace-line');
        if (paths.length === 0) return;

        resultado.debug.push('SVG[' + idx + '] ' + bbox.width.toFixed(0) + 'x' + bbox.height.toFixed(0) + ' paths=' + paths.length);

        // Pegar viewBox
        const viewBox = svg.getAttribute('viewBox') || '';
        const vbParts = viewBox.split(/[\\s,]+/).map(Number);

        paths.forEach((path, pidx) => {
            const cor = window.getComputedStyle(path).stroke || path.getAttribute('stroke') || '';
            const d = path.getAttribute('d') || '';
            if (d.length < 50) return;

            // Extrair coordenadas
            const coords = [];
            const matches = d.matchAll(/[ML]\\s*([\\d.eE+-]+)[,\\s]([\\d.eE+-]+)/gi);
            for (const m of matches) {
                coords.push({
                    px: parseFloat(m[1]),
                    py: parseFloat(m[2])
                });
            }

            if (coords.length > 5) {
                // Pegar as dimensoes da area de plot
                // (o SVG do grafico, nao o SVG do eixo)
                let plotWidth = bbox.width;
                let plotHeight = bbox.height;

                if (vbParts.length === 4) {
                    plotWidth = vbParts[2];
                    plotHeight = vbParts[3];
                }

                resultado.graficos.push({
                    svg_idx: idx,
                    cor: cor,
                    num_pontos: coords.length,
                    coords: coords,
                    plot_width: plotWidth,
                    plot_height: plotHeight,
                    viewBox: viewBox,
                    svg_bbox: {
                        x: bbox.x, y: bbox.y,
                        w: bbox.width, h: bbox.height
                    }
                });
                resultado.debug.push('  trace-line cor=' + cor + ' pontos=' + coords.length);
            }
        });

        // Tentar pegar os labels dos eixos Y
        const yTexts = svg.querySelectorAll('text');
        const yValues = [];
        yTexts.forEach(t => {
            const val = parseFloat((t.textContent || '').trim().replace(',', '.'));
            if (!isNaN(val)) {
                const tb = t.getBoundingClientRect();
                yValues.push({ val: val, y: tb.y, x: tb.x });
            }
        });
        if (yValues.length > 1) {
            resultado.eixos_y.push({
                svg_idx: idx,
                valores: yValues.sort((a,b) => a.y - b.y)
            });
        }
    });

    // Pegar eixo X (timestamps) - geralmente no SVG do eixo X
    // Tambem buscar nos eixos dos trends
    const allTexts = document.querySelectorAll('text');
    const timestamps = [];
    allTexts.forEach(t => {
        const txt = (t.textContent || '').trim();
        // Formato tipico: "09/04/2026 04:04:31" ou "8 h" ou "04:00"
        if (txt.match(/\\d{2}[\\/:]\\d{2}/)) {
            const tb = t.getBoundingClientRect();
            timestamps.push({ texto: txt, x: tb.x, y: tb.y });
        }
    });
    resultado.eixos_x = timestamps;

    // Valores de texto grandes (leituras atuais)
    const allElements = document.querySelectorAll('*');
    allElements.forEach(el => {
        if (el.children.length > 2) return;
        const text = (el.innerText || '').trim();
        if (!text || text.length > 20 || text.length < 1) return;
        const match = text.match(/^[\\d]+[,.]?[\\d]*$/);
        if (match) {
            const valor = parseFloat(text.replace(',', '.'));
            if (valor >= 0 && valor <= 1000) {
                const rect = el.getBoundingClientRect();
                resultado.valores_texto.push({
                    texto: text, valor: valor,
                    x: rect.x, y: rect.y,
                    w: rect.width, h: rect.height,
                    fontSize: window.getComputedStyle(el).fontSize,
                    color: window.getComputedStyle(el).color
                });
            }
        }
    });

    return resultado;
}
"""


def coletar():
    log.info("=" * 60)
    log.info("COLETA V2 - ETA TAPACURA (CALIBRADO)")
    log.info("=" * 60)

    Path("dados").mkdir(exist_ok=True)

    with sync_playwright() as p:
        log.info("Iniciando navegador...")
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-gpu", "--ignore-certificate-errors"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
            http_credentials={"username": USUARIO, "password": SENHA}
        )
        page = context.new_page()

        try:
            log.info("Acessando PI Vision...")
            page.goto(URL, timeout=60000, wait_until="networkidle")

            # Verificar login
            login_field = page.query_selector("input[type='text'], input[name='username']")
            password_field = page.query_selector("input[type='password']")
            if login_field and password_field:
                log.info("Login encontrado, preenchendo...")
                login_field.fill(USUARIO)
                password_field.fill(SENHA)
                submit = page.query_selector("button[type='submit'], input[type='submit']")
                if submit:
                    submit.click()
                else:
                    password_field.press("Enter")
                page.wait_for_timeout(8000)

            log.info("Aguardando graficos (15s)...")
            page.wait_for_timeout(15000)

            # Screenshot
            page.screenshot(path=ARQUIVO_SCREENSHOT, full_page=True)
            log.info(f"Screenshot salvo")

            # Extrair dados
            log.info("Extraindo SVG dos graficos...")
            resultado = page.evaluate(JS_EXTRAIR_GRAFICOS)

            for msg in resultado.get('debug', []):
                log.info(f"  [JS] {msg}")

            # Processar graficos
            coleta = processar(resultado)

            # Salvar debug
            try:
                with open(ARQUIVO_DEBUG, "w", encoding="utf-8") as f:
                    json.dump(resultado, f, indent=2, ensure_ascii=False)
                log.info("Debug salvo")
            except Exception as e:
                log.warning(f"Erro debug: {e}")

            salvar(coleta)

        except Exception as e:
            log.error(f"ERRO: {e}")
            try:
                page.screenshot(path=ARQUIVO_SCREENSHOT, full_page=True)
            except:
                pass
            salvar({
                "timestamp": datetime.now().isoformat(),
                "tipo": "erro",
                "erro": str(e),
                "series": {}
            })
        finally:
            browser.close()
            log.info("Navegador fechado.")

    log.info("COLETA FINALIZADA")


def processar(resultado):
    """Processa os graficos SVG e converte pixel -> valores reais."""
    agora = datetime.now()
    coleta = {
        "timestamp": agora.isoformat(),
        "tipo": "completa",
        "valores_atuais": {},
        "series": {}
    }

    graficos = resultado.get("graficos", [])
    eixos_y = resultado.get("eixos_y", [])
    eixos_x = resultado.get("eixos_x", [])
    valores_texto = resultado.get("valores_texto", [])

    log.info(f"Graficos trace-line: {len(graficos)}")
    log.info(f"Eixos Y encontrados: {len(eixos_y)}")
    log.info(f"Timestamps no eixo X: {len(eixos_x)}")
    log.info(f"Valores texto na tela: {len(valores_texto)}")

    # Descobrir range de tempo do eixo X
    # Tipicamente o PI Vision mostra 8h
    # Vamos tentar extrair dos timestamps do eixo X
    tempo_inicio = None
    tempo_fim = None

    for ts in eixos_x:
        txt = ts["texto"]
        try:
            # Tentar formato "09/04/2026 04:04:31"
            dt = datetime.strptime(txt, "%d/%m/%Y %H:%M:%S")
            if tempo_inicio is None or dt < tempo_inicio:
                tempo_inicio = dt
            if tempo_fim is None or dt > tempo_fim:
                tempo_fim = dt
        except ValueError:
            try:
                # Formato "04:04" ou "08:00"
                dt = datetime.strptime(txt, "%H:%M")
                dt = dt.replace(year=agora.year, month=agora.month, day=agora.day)
                if tempo_inicio is None or dt < tempo_inicio:
                    tempo_inicio = dt
                if tempo_fim is None or dt > tempo_fim:
                    tempo_fim = dt
            except ValueError:
                pass

    if tempo_inicio and tempo_fim:
        log.info(f"Range de tempo: {tempo_inicio} -> {tempo_fim}")
    else:
        # Fallback: assumir 8h atras ate agora
        tempo_fim = agora
        tempo_inicio = agora - timedelta(hours=8)
        log.info(f"Range de tempo (estimado): {tempo_inicio} -> {tempo_fim}")

    duracao_total = (tempo_fim - tempo_inicio).total_seconds()
    if duracao_total <= 0:
        duracao_total = 8 * 3600  # fallback 8h

    # Processar cada grafico trace-line
    for graf in graficos:
        cor = graf["cor"].strip()
        parametro = CORES_PARAMETROS.get(cor)

        if not parametro:
            # Tentar match parcial
            for cor_ref, param in CORES_PARAMETROS.items():
                if cor_ref.replace(" ", "") == cor.replace(" ", ""):
                    parametro = param
                    break

        if not parametro:
            log.warning(f"  Cor nao mapeada: {cor} ({graf['num_pontos']} pontos)")
            continue

        escala = ESCALAS.get(parametro, {"min": 0, "max": 100})
        coords = graf["coords"]
        plot_w = graf["plot_width"]
        plot_h = graf["plot_height"]

        if plot_w <= 0 or plot_h <= 0:
            log.warning(f"  {parametro}: dimensoes invalidas {plot_w}x{plot_h}")
            continue

        # Tentar calibrar eixo Y com os textos do eixo
        y_min_val = escala["min"]
        y_max_val = escala["max"]

        # Procurar eixo Y correspondente a este SVG
        for eixo in eixos_y:
            if eixo["svg_idx"] == graf["svg_idx"]:
                vals = [v["val"] for v in eixo["valores"]]
                if vals:
                    y_min_val = min(vals)
                    y_max_val = max(vals)
                    log.info(f"  {parametro}: eixo Y calibrado {y_min_val}-{y_max_val}")
                break

        # Converter coordenadas de pixel para valores reais
        serie = []
        for coord in coords:
            px = coord["px"]
            py = coord["py"]

            # X -> tempo (px=0 = inicio, px=plot_w = fim)
            frac_x = px / plot_w if plot_w > 0 else 0
            frac_x = max(0, min(1, frac_x))
            ts = tempo_inicio + timedelta(seconds=frac_x * duracao_total)

            # Y -> valor (py=0 = topo = max, py=plot_h = base = min)
            # Em SVG, Y cresce pra baixo
            frac_y = 1.0 - (py / plot_h) if plot_h > 0 else 0
            frac_y = max(0, min(1, frac_y))
            valor = y_min_val + frac_y * (y_max_val - y_min_val)

            serie.append({
                "t": ts.isoformat(),
                "v": round(valor, 4)
            })

        # Ordenar por tempo
        serie.sort(key=lambda d: d["t"])

        # Remover duplicatas (mesmo timestamp)
        seen = set()
        serie_unica = []
        for d in serie:
            key = d["t"][:16]  # precisao de minuto
            if key not in seen:
                seen.add(key)
                serie_unica.append(d)

        coleta["series"][parametro] = {
            "num_pontos": len(serie_unica),
            "cor_svg": cor,
            "escala_y": {"min": y_min_val, "max": y_max_val},
            "dados": serie_unica
        }

        # Valor atual = ultimo ponto
        if serie_unica:
            coleta["valores_atuais"][parametro] = serie_unica[-1]["v"]

        log.info(f"  {parametro}: {len(serie_unica)} pontos, "
                 f"ultimo={serie_unica[-1]['v'] if serie_unica else 'N/A'}")

    # Valores de texto (complementar)
    log.info("Valores de texto na tela:")
    for v in valores_texto:
        log.info(f"  '{v['texto']}' val={v['valor']} pos=({v['x']:.0f},{v['y']:.0f}) "
                 f"fontSize={v['fontSize']} color={v['color']}")

    return coleta


def salvar(coleta):
    """Salva no JSON acumulando historico."""
    historico = []
    if os.path.exists(ARQUIVO_DADOS):
        try:
            with open(ARQUIVO_DADOS, "r", encoding="utf-8") as f:
                historico = json.load(f)
        except:
            historico = []

    historico.append(coleta)

    # Manter ultimos 30 dias (~360 coletas de 2h)
    if len(historico) > 360:
        historico = historico[-360:]

    with open(ARQUIVO_DADOS, "w", encoding="utf-8") as f:
        json.dump(historico, f, indent=2, ensure_ascii=False)

    log.info(f"Salvo. Total de coletas: {len(historico)}")

    # Stats
    total_pontos = sum(
        s.get("num_pontos", 0)
        for s in coleta.get("series", {}).values()
    )
    log.info(f"Total de pontos nesta coleta: {total_pontos}")


if __name__ == "__main__":
    coletar()
