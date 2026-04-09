"""
=============================================================
COLETOR ETA TAPACURA - VERSAO COMPLETA (GRAFICOS)
=============================================================
Extrai TODOS os pontos dos graficos do PI Vision,
nao apenas o valor atual. Cada coleta pega ~8h de dados
com resolucao de minutos.

Usa Playwright + extracao de SVG/Canvas.
=============================================================
"""

import json
import os
import sys
import time
import re
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
# ============================================================


# JavaScript que roda DENTRO do navegador para extrair dados dos graficos
# PI Vision usa SVG para renderizar trends (graficos de linha)
JS_EXTRAIR_GRAFICOS = """
() => {
    const resultado = {
        metodo: null,
        graficos: {},
        valores_tela: {},
        debug: []
    };

    // =============================================
    // METODO 1: Extrair dados via SVG paths/polylines
    // =============================================
    try {
        // PI Vision geralmente tem elementos com classes como:
        // .trend-line, .data-line, path, polyline dentro de SVG
        const svgs = document.querySelectorAll('svg');
        resultado.debug.push(`SVGs encontrados: ${svgs.length}`);

        svgs.forEach((svg, idx) => {
            // Pegar dimensoes do SVG
            const bbox = svg.getBoundingClientRect();
            resultado.debug.push(`SVG[${idx}] dimensoes: ${bbox.width}x${bbox.height} em (${bbox.x},${bbox.y})`);

            // Buscar paths (linhas dos graficos)
            const paths = svg.querySelectorAll('path[d], polyline[points]');
            resultado.debug.push(`SVG[${idx}] paths/polylines: ${paths.length}`);

            paths.forEach((path, pidx) => {
                const classe = path.getAttribute('class') || '';
                const cor = window.getComputedStyle(path).stroke || path.getAttribute('stroke') || '';
                const d = path.getAttribute('d') || '';
                const points = path.getAttribute('points') || '';

                // Ignorar paths muito curtos (eixos, grades)
                const dataStr = d || points;
                if (dataStr.length < 50) return;

                resultado.debug.push(`  Path[${pidx}] cor=${cor} classe=${classe} tam=${dataStr.length}`);

                // Extrair coordenadas do path
                let coordenadas = [];

                if (d) {
                    // Parse SVG path "M x,y L x,y L x,y..."
                    const matches = d.matchAll(/[ML]\\s*([\\d.eE+-]+)[,\\s]([\\d.eE+-]+)/gi);
                    for (const m of matches) {
                        coordenadas.push({
                            x: parseFloat(m[1]),
                            y: parseFloat(m[2])
                        });
                    }
                }

                if (points) {
                    // Parse polyline "x,y x,y x,y"
                    const pares = points.trim().split(/\\s+/);
                    for (const par of pares) {
                        const [x, y] = par.split(',').map(Number);
                        if (!isNaN(x) && !isNaN(y)) {
                            coordenadas.push({ x, y });
                        }
                    }
                }

                if (coordenadas.length > 5) {
                    const key = `svg${idx}_path${pidx}`;
                    resultado.graficos[key] = {
                        cor: cor,
                        classe: classe,
                        num_pontos: coordenadas.length,
                        coordenadas: coordenadas,
                        svg_width: bbox.width,
                        svg_height: bbox.height,
                        svg_viewBox: svg.getAttribute('viewBox') || ''
                    };
                    resultado.metodo = 'svg_path';
                }
            });
        });
    } catch(e) {
        resultado.debug.push(`Erro SVG: ${e.message}`);
    }

    // =============================================
    // METODO 2: Interceptar dados via PI Web API
    // (PI Vision faz requests XHR para pegar dados)
    // =============================================
    try {
        // Verificar se ha dados no performance entries
        const entries = performance.getEntriesByType('resource');
        const apiCalls = entries.filter(e =>
            e.name.includes('/piwebapi/') ||
            e.name.includes('/streams/') ||
            e.name.includes('/recorded') ||
            e.name.includes('/interpolated') ||
            e.name.includes('/plot')
        );
        resultado.debug.push(`PI Web API calls encontradas: ${apiCalls.length}`);
        apiCalls.forEach(c => resultado.debug.push(`  API: ${c.name.substring(0, 150)}`));
    } catch(e) {
        resultado.debug.push(`Erro API check: ${e.message}`);
    }

    // =============================================
    // METODO 3: Extrair dados de Canvas (se SVG nao funcionar)
    // =============================================
    try {
        const canvases = document.querySelectorAll('canvas');
        resultado.debug.push(`Canvas encontrados: ${canvases.length}`);
        // Canvas nao da pra extrair pontos facilmente,
        // mas registramos pra debug
    } catch(e) {
        resultado.debug.push(`Erro Canvas: ${e.message}`);
    }

    // =============================================
    // METODO 4: Buscar dados em variaveis JS globais
    // (PI Vision pode guardar dados em escopo Angular/React)
    // =============================================
    try {
        // PI Vision usa AngularJS — tentar acessar os scopes
        const angular = window.angular;
        if (angular) {
            resultado.debug.push('AngularJS detectado!');

            // Buscar elementos com ng-scope
            const scopes = document.querySelectorAll('[ng-scope], [data-ng-scope], .ng-scope');
            resultado.debug.push(`Elementos com ng-scope: ${scopes.length}`);

            scopes.forEach((el, idx) => {
                try {
                    const scope = angular.element(el).scope();
                    if (scope) {
                        // Procurar por dados de trend/chart
                        const keys = Object.keys(scope).filter(k =>
                            !k.startsWith('$') &&
                            !k.startsWith('_') &&
                            typeof scope[k] !== 'function'
                        );

                        keys.forEach(k => {
                            const val = scope[k];
                            if (val && typeof val === 'object') {
                                // Procurar arrays de dados
                                if (Array.isArray(val) && val.length > 5) {
                                    const sample = val.slice(0, 3);
                                    resultado.debug.push(`  scope[${idx}].${k} = Array[${val.length}] amostra: ${JSON.stringify(sample).substring(0, 200)}`);

                                    // Se parece com dados de serie temporal
                                    if (val[0] && (val[0].Value !== undefined || val[0].value !== undefined || val[0].y !== undefined)) {
                                        resultado.graficos[`angular_${k}`] = {
                                            fonte: 'angular_scope',
                                            dados: val.slice(0, 1000), // limitar tamanho
                                            num_pontos: val.length
                                        };
                                        resultado.metodo = 'angular_scope';
                                    }
                                }
                                // Objeto com propriedade Items/items (padrao PI Web API)
                                if (val.Items || val.items) {
                                    const items = val.Items || val.items;
                                    resultado.debug.push(`  scope[${idx}].${k}.Items = Array[${items.length}]`);
                                    if (items.length > 5) {
                                        resultado.graficos[`angular_${k}_items`] = {
                                            fonte: 'angular_scope_items',
                                            dados: items.slice(0, 1000),
                                            num_pontos: items.length
                                        };
                                        resultado.metodo = 'angular_scope';
                                    }
                                }
                            }
                        });
                    }
                } catch(e) {}
            });
        } else {
            resultado.debug.push('AngularJS NAO detectado');
        }
    } catch(e) {
        resultado.debug.push(`Erro Angular: ${e.message}`);
    }

    // =============================================
    // SEMPRE: Extrair valores de texto visiveis na tela
    // =============================================
    try {
        // Pegar todos elementos com texto que parecem valores
        const allElements = document.querySelectorAll('*');
        const textValues = [];

        allElements.forEach(el => {
            if (el.children.length > 2) return; // pular containers
            const text = (el.innerText || '').trim();
            if (!text || text.length > 30 || text.length < 1) return;

            // Formato: numero com virgula ou ponto
            const match = text.match(/^[\\d]+[,.]?[\\d]*$/);
            if (match) {
                const valor = parseFloat(text.replace(',', '.'));
                if (valor >= 0 && valor <= 1000) {
                    const rect = el.getBoundingClientRect();
                    textValues.push({
                        texto: text,
                        valor: valor,
                        classe: (el.className || '').toString().substring(0, 100),
                        id: (el.id || '').substring(0, 50),
                        tag: el.tagName,
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height
                    });
                }
            }
        });

        resultado.valores_tela = textValues;
        resultado.debug.push(`Valores de texto na tela: ${textValues.length}`);

    } catch(e) {
        resultado.debug.push(`Erro texto: ${e.message}`);
    }

    // =============================================
    // Pegar HTML dos graficos pra debug
    // =============================================
    try {
        const trendAreas = document.querySelectorAll(
            '[class*="trend"], [class*="chart"], [class*="plot"], ' +
            '[class*="Trend"], [class*="Chart"], [class*="Plot"], ' +
            '[class*="graph"], [class*="Graph"]'
        );
        resultado.debug.push(`Elementos trend/chart/plot: ${trendAreas.length}`);
        trendAreas.forEach((el, idx) => {
            resultado.debug.push(`  [${idx}] tag=${el.tagName} class=${(el.className || '').toString().substring(0, 80)}`);
        });
    } catch(e) {}

    return resultado;
}
""";


def coletar():
    log.info("=" * 60)
    log.info("INICIANDO COLETA COMPLETA (COM GRAFICOS)")
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

        # Interceptar chamadas de API do PI Vision
        api_responses = []

        def capturar_response(response):
            """Captura respostas da PI Web API automaticamente."""
            url = response.url
            if any(kw in url.lower() for kw in
                   ['/piwebapi/', '/streams/', '/recorded',
                    '/interpolated', '/plot', '/summary']):
                try:
                    body = response.json()
                    api_responses.append({
                        "url": url[:300],
                        "status": response.status,
                        "dados": body
                    })
                    log.info(f"  API capturada: {url[:100]}...")
                except:
                    pass

        page.on("response", capturar_response)

        try:
            # Acessar pagina
            log.info("Acessando PI Vision...")
            page.goto(URL, timeout=60000, wait_until="networkidle")
            log.info("Pagina carregou. Aguardando renderizacao...")

            # Verificar login
            login_field = page.query_selector(
                "input[type='text'], input[name='username'], "
                "input[placeholder*='usu'], input[placeholder*='user']"
            )
            password_field = page.query_selector("input[type='password']")

            if login_field and password_field:
                log.info("Formulario de login detectado...")
                login_field.fill(USUARIO)
                password_field.fill(SENHA)
                submit = page.query_selector(
                    "button[type='submit'], input[type='submit']"
                )
                if submit:
                    submit.click()
                else:
                    password_field.press("Enter")
                page.wait_for_timeout(8000)
                log.info("Login feito.")

            # Esperar os graficos renderizarem completamente
            log.info("Aguardando graficos renderizarem (15s)...")
            page.wait_for_timeout(15000)

            # Screenshot
            page.screenshot(path=ARQUIVO_SCREENSHOT, full_page=True)
            log.info(f"Screenshot: {ARQUIVO_SCREENSHOT}")

            # Extrair dados via JavaScript
            log.info("Executando extracao de dados...")
            resultado_js = page.evaluate(JS_EXTRAIR_GRAFICOS)

            # Log de debug
            log.info(f"Metodo de extracao: {resultado_js.get('metodo', 'nenhum')}")
            log.info(f"Graficos encontrados: {len(resultado_js.get('graficos', {}))}")
            log.info(f"Valores de texto: {len(resultado_js.get('valores_tela', []))}")
            log.info(f"APIs interceptadas: {len(api_responses)}")

            for msg in resultado_js.get('debug', []):
                log.info(f"  [JS] {msg}")

            # Processar os dados coletados
            coleta = processar_resultado(resultado_js, api_responses)

            # Salvar debug HTML pra analise
            try:
                # Pegar HTML das areas de grafico
                html_graficos = page.evaluate("""
                    () => {
                        const areas = document.querySelectorAll(
                            'svg, [class*="trend"], [class*="chart"], [class*="value"], [class*="Value"]'
                        );
                        let html = '';
                        areas.forEach((el, i) => {
                            html += `\\n<!-- ELEMENTO ${i}: ${el.tagName} class="${el.className}" -->\\n`;
                            html += el.outerHTML.substring(0, 5000) + '\\n';
                        });
                        return html;
                    }
                """)
                with open(ARQUIVO_DEBUG, "w", encoding="utf-8") as f:
                    f.write(html_graficos[:500000])  # limitar tamanho
                log.info(f"Debug HTML salvo: {ARQUIVO_DEBUG}")
            except Exception as e:
                log.warning(f"Erro ao salvar debug: {e}")

            # Salvar
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
                "valores_atuais": {},
                "series": {}
            })
        finally:
            browser.close()
            log.info("Navegador fechado.")

    log.info("COLETA FINALIZADA")


def processar_resultado(resultado_js, api_responses):
    """Processa os dados brutos e organiza em formato final."""
    agora = datetime.now()
    coleta = {
        "timestamp": agora.isoformat(),
        "tipo": "completa",
        "valores_atuais": {},
        "series": {}
    }

    # 1. Processar valores de texto (leitura atual)
    valores_tela = resultado_js.get("valores_tela", [])
    if valores_tela:
        # Classificar por posicao Y na tela (os 4 parametros ficam empilhados)
        valores_tela.sort(key=lambda v: v.get("y", 0))

        # Tentar mapear por posicao ou contexto
        for v in valores_tela:
            ctx = f"{v.get('classe', '')} {v.get('id', '')}".lower()
            valor = v["valor"]

            if "cloro" in ctx:
                coleta["valores_atuais"]["cloro_ppm"] = valor
            elif "cor" in ctx and "corpo" not in ctx:
                coleta["valores_atuais"]["cor_uc"] = valor
            elif "turb" in ctx:
                coleta["valores_atuais"]["turbidez_ntu"] = valor
            elif "ph" in ctx and "phone" not in ctx:
                coleta["valores_atuais"]["ph"] = valor

        log.info(f"Valores atuais: {coleta['valores_atuais']}")

    # 2. Processar dados de API interceptada (MELHOR FONTE!)
    if api_responses:
        log.info(f"Processando {len(api_responses)} respostas de API...")
        for resp in api_responses:
            try:
                dados = resp["dados"]
                url = resp["url"].lower()

                # PI Web API retorna Items com Timestamp + Value
                items = None
                nome = "desconhecido"

                if isinstance(dados, dict):
                    items = dados.get("Items", dados.get("items", []))
                    nome = dados.get("Name", dados.get("name",
                           dados.get("Label", "desconhecido")))

                if items and len(items) > 0:
                    serie = []
                    for item in items:
                        ts = item.get("Timestamp", item.get("timestamp", ""))
                        val = item.get("Value", item.get("value", None))

                        # Value pode ser um objeto com sub-propriedades
                        if isinstance(val, dict):
                            val = val.get("Value", val.get("value", None))

                        if ts and val is not None:
                            try:
                                serie.append({
                                    "t": ts,
                                    "v": float(val) if not isinstance(val, bool) else None
                                })
                            except (ValueError, TypeError):
                                pass

                    if serie:
                        # Identificar parametro pelo nome ou URL
                        param = identificar_parametro(nome, url)
                        coleta["series"][param] = {
                            "nome_original": nome,
                            "num_pontos": len(serie),
                            "dados": serie
                        }
                        log.info(f"  Serie '{param}': {len(serie)} pontos (nome={nome})")

            except Exception as e:
                log.warning(f"  Erro processando API response: {e}")

    # 3. Processar dados de SVG (se nao tiver dados de API)
    graficos = resultado_js.get("graficos", {})
    if graficos and not coleta["series"]:
        log.info(f"Processando {len(graficos)} graficos SVG...")
        for key, grafico in graficos.items():
            fonte = grafico.get("fonte", "svg")

            if fonte == "angular_scope" or fonte == "angular_scope_items":
                # Dados do Angular - ja estruturados
                dados_brutos = grafico.get("dados", [])
                serie = []
                for item in dados_brutos:
                    ts = item.get("Timestamp", item.get("timestamp",
                         item.get("t", item.get("Time", ""))))
                    val = item.get("Value", item.get("value",
                          item.get("v", item.get("y", None))))
                    if isinstance(val, dict):
                        val = val.get("Value", None)
                    if ts and val is not None:
                        try:
                            serie.append({"t": str(ts), "v": float(val)})
                        except:
                            pass
                if serie:
                    coleta["series"][key] = {
                        "fonte": fonte,
                        "num_pontos": len(serie),
                        "dados": serie
                    }
                    log.info(f"  Serie Angular '{key}': {len(serie)} pontos")

            elif "coordenadas" in grafico:
                # Dados SVG - coordenadas de pixel (precisa converter)
                coords = grafico["coordenadas"]
                coleta["series"][key] = {
                    "fonte": "svg_coordenadas",
                    "cor": grafico.get("cor", ""),
                    "num_pontos": len(coords),
                    "svg_width": grafico.get("svg_width", 0),
                    "svg_height": grafico.get("svg_height", 0),
                    "coordenadas_pixel": coords[:1000]  # limitar
                }
                log.info(f"  Serie SVG '{key}': {len(coords)} pontos (cor={grafico.get('cor', '')})")

    return coleta


def identificar_parametro(nome, url):
    """Identifica qual parametro (cloro/cor/turbidez/ph) baseado no nome."""
    texto = f"{nome} {url}".lower()

    if "cloro" in texto or "cl2" in texto or "residual" in texto:
        return "cloro_ppm"
    elif "cor" in texto and "corpo" not in texto:
        return "cor_uc"
    elif "turb" in texto or "ntu" in texto:
        return "turbidez_ntu"
    elif "ph" in texto:
        return "ph"
    else:
        return f"param_{nome[:20]}"


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

    # Manter ultimos 30 dias (360 coletas de 2h)
    if len(historico) > 360:
        historico = historico[-360:]

    with open(ARQUIVO_DADOS, "w", encoding="utf-8") as f:
        json.dump(historico, f, indent=2, ensure_ascii=False)

    log.info(f"Salvo. Total de coletas: {len(historico)}")


if __name__ == "__main__":
    coletar()
