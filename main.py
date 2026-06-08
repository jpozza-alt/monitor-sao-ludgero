# ============================================================
# CEMADEN - integração diagnóstica
# Monitor São Ludgero API - v1.10-cemaden-diagnostico
# ============================================================

CEMADEN_GRAPH_URL = "https://resources.cemaden.gov.br/graficos/interativo/grafico_CEMADEN.php"

SAO_LUDGERO_REF = {
    "municipio": "São Ludgero",
    "uf": "SC",
    "latitude": -28.3269,
    "longitude": -49.1764,
}

# Coordenadas aproximadas por município/região apenas para triagem inicial.
# O modo definitivo deverá usar coordenadas reais das PCDs quando forem extraídas
# de uma fonte geoespacial estável.
CEMADEN_ESTACOES_CANDIDATAS = [
    {
        "idpcd": 8481,
        "uf": "SC",
        "cidade": "BRAÇO DO NORTE",
        "nome": "Centro",
        "latitude": -28.2750,
        "longitude": -49.1650,
        "prioridade": 1,
        "observacao": "Mais próxima de São Ludgero por município vizinho; pode estar sem dados recentes.",
    },
    {
        "idpcd": 8734,
        "uf": "SC",
        "cidade": "PEDRAS GRANDES",
        "nome": "Rio Tubarao",
        "latitude": -28.4330,
        "longitude": -49.1850,
        "prioridade": 2,
        "observacao": "Estação em município próximo na bacia do Tubarão.",
    },
    {
        "idpcd": 6972,
        "uf": "SC",
        "cidade": "ORLEANS",
        "nome": "Três Barras",
        "latitude": -28.3600,
        "longitude": -49.2900,
        "prioridade": 3,
        "observacao": "Estação próxima, útil como redundância.",
    },
    {
        "idpcd": 6971,
        "uf": "SC",
        "cidade": "ORLEANS",
        "nome": "Centro",
        "latitude": -28.3580,
        "longitude": -49.2920,
        "prioridade": 4,
        "observacao": "Estação próxima, útil como redundância.",
    },
    {
        "idpcd": 7400,
        "uf": "SC",
        "cidade": "GRÃO PARÁ",
        "nome": "Unidade Sanitária Central",
        "latitude": -28.1850,
        "longitude": -49.2150,
        "prioridade": 5,
        "observacao": "Estação de apoio regional.",
    },
    {
        "idpcd": 7002,
        "uf": "SC",
        "cidade": "SÃO MARTINHO",
        "nome": "Garagem da Prefeitura",
        "latitude": -28.1600,
        "longitude": -48.9850,
        "prioridade": 6,
        "observacao": "Estação de apoio regional.",
    },
    {
        "idpcd": 8510,
        "uf": "SC",
        "cidade": "TUBARÃO",
        "nome": "São Martinho",
        "latitude": -28.4700,
        "longitude": -49.0200,
        "prioridade": 7,
        "observacao": "Estação de apoio regional.",
    },
]

CEMADEN_HEADERS = {
    "User-Agent": "Monitor-Sao-Ludgero-API/1.10 (+https://monitor-sao-ludgero.onrender.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _cemaden_float(valor: Any) -> float | None:
    if valor is None:
        return None

    texto = str(valor).strip()
    if not texto or texto in {"-", "--", "null", "None", "Sem Dados"}:
        return None

    texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _texto_limpo_html(html: str) -> str:
    texto = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html, flags=re.I)
    texto = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = unescape(texto)
    texto = texto.replace("\xa0", " ")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    raio_terra_km = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return raio_terra_km * c


def _parse_datahora_utc(valor: str | None) -> dict[str, Any]:
    if not valor:
        return {
            "datahora_utc_iso": None,
            "idade_horas": None,
            "parse_ok": False,
        }

    texto = valor.strip()
    formatos = [
        "%d/%m/%y %H:%M",
        "%d/%m/%Y %H:%M",
    ]

    for formato in formatos:
        try:
            dt = datetime.strptime(texto, formato).replace(tzinfo=timezone.utc)
            idade = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            return {
                "datahora_utc_iso": dt.isoformat(),
                "idade_horas": round(idade, 2),
                "parse_ok": True,
            }
        except ValueError:
            pass

    # Formato comum em alguns títulos: 05/06/2026 8h UTC
    m = re.search(
        r"(?P<data>\d{2}/\d{2}/\d{4})\s+(?P<hora>\d{1,2})h(?:\s*UTC)?",
        texto,
        flags=re.I,
    )
    if m:
        try:
            dt = datetime.strptime(
                f"{m.group('data')} {int(m.group('hora')):02d}:00",
                "%d/%m/%Y %H:%M",
            ).replace(tzinfo=timezone.utc)
            idade = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            return {
                "datahora_utc_iso": dt.isoformat(),
                "idade_horas": round(idade, 2),
                "parse_ok": True,
            }
        except ValueError:
            pass

    return {
        "datahora_utc_iso": None,
        "idade_horas": None,
        "parse_ok": False,
    }


def _extrair_linha_acumulados(texto: str, estacao: dict[str, Any]) -> dict[str, Any] | None:
    """
    Tenta extrair a linha da tabela:
    UF, Cidade, Nome, Data, Último, 1, 6, 12, 24, 48, 72, 96
    """

    uf = re.escape(estacao["uf"])
    cidade = re.escape(estacao["cidade"])
    nome = re.escape(estacao["nome"])
    sep = r"\s*,?\s*"
    numero = r"(?:-|\d+(?:[.,]\d+)?)"

    padrao = (
        rf"\b{uf}{sep}{cidade}{sep}{nome}{sep}"
        rf"(?P<datahora>\d{{2}}/\d{{2}}/\d{{2,4}}\s+\d{{2}}:\d{{2}}){sep}"
        rf"(?P<ultimo>{numero}){sep}"
        rf"(?P<acc1>{numero}){sep}"
        rf"(?P<acc6>{numero}){sep}"
        rf"(?P<acc12>{numero}){sep}"
        rf"(?P<acc24>{numero}){sep}"
        rf"(?P<acc48>{numero}){sep}"
        rf"(?P<acc72>{numero}){sep}"
        rf"(?P<acc96>{numero})"
    )

    m = re.search(padrao, texto, flags=re.I)
    if not m:
        return None

    datahora = m.group("datahora")
    tempo = _parse_datahora_utc(datahora)

    return {
        "metodo_extracao": "linha_tabela_acumulados",
        "datahora_utc_texto": datahora,
        "datahora_utc_iso": tempo["datahora_utc_iso"],
        "idade_horas": tempo["idade_horas"],
        "chuva_observada_mm": _cemaden_float(m.group("ultimo")),
        "acumulado_1h_mm": _cemaden_float(m.group("acc1")),
        "acumulado_6h_mm": _cemaden_float(m.group("acc6")),
        "acumulado_12h_mm": _cemaden_float(m.group("acc12")),
        "acumulado_24h_mm": _cemaden_float(m.group("acc24")),
        "acumulado_48h_mm": _cemaden_float(m.group("acc48")),
        "acumulado_72h_mm": _cemaden_float(m.group("acc72")),
        "acumulado_96h_mm": _cemaden_float(m.group("acc96")),
    }


def _extrair_metadados_grafico(texto: str) -> dict[str, Any] | None:
    """
    Fallback: extrai informações do título do gráfico.
    Normalmente não traz 1h/24h/72h, mas ajuda no diagnóstico.
    """

    meta: dict[str, Any] = {
        "metodo_extracao": "metadados_grafico_fallback",
    }

    m = re.search(
        r"Estação:\s*(?P<nome>[^|()]+?)\s*\((?P<codigo>[A-Z0-9]+)\).*?"
        r"Município:\s*(?P<cidade>[^/|]+)\s*/\s*(?P<uf>[A-Z]{2})",
        texto,
        flags=re.I,
    )
    if m:
        meta["nome_estacao_detectado"] = m.group("nome").strip()
        meta["codigo_estacao_detectado"] = m.group("codigo").strip()
        meta["cidade_detectada"] = m.group("cidade").strip()
        meta["uf_detectada"] = m.group("uf").strip()

    m = re.search(
        r"Atualização:\s*(?P<datahora>\d{2}/\d{2}/\d{2,4}\s+\d{1,2}(?::\d{2}|h)(?:\s*UTC)?)",
        texto,
        flags=re.I,
    )
    if m:
        datahora = m.group("datahora").replace("h", ":00").replace(" UTC", "").strip()
        tempo = _parse_datahora_utc(datahora)
        meta["datahora_utc_texto"] = m.group("datahora").strip()
        meta["datahora_utc_iso"] = tempo["datahora_utc_iso"]
        meta["idade_horas"] = tempo["idade_horas"]

    m = re.search(r"Precipitação:\s*(?P<mm>\d+(?:[.,]\d+)?)\s*mm", texto, flags=re.I)
    if m:
        meta["chuva_observada_mm"] = _cemaden_float(m.group("mm"))

    if len(meta) == 1:
        return None

    meta.setdefault("acumulado_1h_mm", None)
    meta.setdefault("acumulado_24h_mm", None)
    meta.setdefault("acumulado_72h_mm", None)

    return meta


def _campos_cemaden_encontrados(html: str, texto: str) -> dict[str, bool]:
    base = html + " " + texto

    return {
        "tem_placeholder_angular": "{{ x.acc1hr }}" in html or "{{ x.uf }}" in html,
        "tem_campo_uf": bool(re.search(r"\bUF\b|x\.uf", base, flags=re.I)),
        "tem_campo_cidade": bool(re.search(r"\bCidade\b|x\.cidade", base, flags=re.I)),
        "tem_campo_nome": bool(re.search(r"\bNome\b|nomeestacao|x\.nomeestacao", base, flags=re.I)),
        "tem_datahora": bool(re.search(r"Data\s*\(Horário UTC\)|datahoraUltimovalor|\d{2}/\d{2}/\d{2,4}", base, flags=re.I)),
        "tem_ultimo": bool(re.search(r"\bÚltimo\b|ultimovalor", base, flags=re.I)),
        "tem_acc1h": bool(re.search(r"acc1hr|\b1\b", base, flags=re.I)),
        "tem_acc24h": bool(re.search(r"acc24hr|\b24\b", base, flags=re.I)),
        "tem_acc72h": bool(re.search(r"acc72hr|\b72\b", base, flags=re.I)),
        "tem_highcharts": "Highcharts" in html,
        "tem_cemaden": "CEMADEN" in base.upper(),
    }


def _com_distancia(estacao: dict[str, Any]) -> dict[str, Any]:
    distancia = _haversine_km(
        SAO_LUDGERO_REF["latitude"],
        SAO_LUDGERO_REF["longitude"],
        estacao["latitude"],
        estacao["longitude"],
    )

    saida = dict(estacao)
    saida["distancia_aproximada_km"] = round(distancia, 2)
    saida["criterio_distancia"] = "aproximado_por_municipio_ou_localidade"
    return saida


async def _consultar_estacao_cemaden(
    client: httpx.AsyncClient,
    estacao: dict[str, Any],
    incluir_amostra: bool = False,
) -> dict[str, Any]:
    estacao_base = _com_distancia(estacao)

    params = {
        "idpcd": str(estacao["idpcd"]),
        "menu": "periodo",
        "uf": estacao["uf"],
    }

    inicio = datetime.now(timezone.utc)

    try:
        resposta = await client.get(
            CEMADEN_GRAPH_URL,
            params=params,
            headers=CEMADEN_HEADERS,
        )

        duracao_ms = int((datetime.now(timezone.utc) - inicio).total_seconds() * 1000)
        html = resposta.text or ""
        texto = _texto_limpo_html(html)

        dados = _extrair_linha_acumulados(texto, estacao)
        if dados is None:
            dados = _extrair_metadados_grafico(texto)

        campos = _campos_cemaden_encontrados(html, texto)

        valores_principais = {}
        if dados:
            valores_principais = {
                "chuva_observada_mm": dados.get("chuva_observada_mm"),
                "acumulado_1h_mm": dados.get("acumulado_1h_mm"),
                "acumulado_24h_mm": dados.get("acumulado_24h_mm"),
                "acumulado_72h_mm": dados.get("acumulado_72h_mm"),
            }

        dados_validos = any(v is not None for v in valores_principais.values())

        resultado = {
            "ok_http": 200 <= resposta.status_code < 300,
            "status_http": resposta.status_code,
            "tempo_resposta_ms": duracao_ms,
            "url_consultada": str(resposta.url),
            "content_type": resposta.headers.get("content-type"),
            "tamanho_resposta": len(html),
            "fonte": "CEMADEN/resources/graficos/interativo/grafico_CEMADEN.php",
            "estacao": estacao_base,
            "campos_encontrados": campos,
            "dados_extraidos": dados,
            "dados_validos": dados_validos,
        }

        if incluir_amostra:
            resultado["amostra_texto_normalizada"] = texto[:1800]

        return resultado

    except Exception as exc:
        return {
            "ok_http": False,
            "status_http": None,
            "erro": str(exc),
            "fonte": "CEMADEN/resources/graficos/interativo/grafico_CEMADEN.php",
            "estacao": estacao_base,
            "dados_extraidos": None,
            "dados_validos": False,
        }


@app.get("/debug-cemaden")
async def debug_cemaden(
    idpcd: int = Query(8481, description="ID público da PCD no gráfico CEMADEN"),
    uf: str = Query("SC", min_length=2, max_length=2, description="UF da estação"),
):
    """
    Diagnóstico bruto da fonte pública do CEMADEN.

    Não deve ser usado diretamente para decisão operacional.
    Serve para verificar status HTTP, estrutura da página e campos presentes.
    """

    estacao_debug = {
        "idpcd": idpcd,
        "uf": uf.upper(),
        "cidade": "BRAÇO DO NORTE" if idpcd == 8481 else "",
        "nome": "Centro" if idpcd == 8481 else "",
        "latitude": SAO_LUDGERO_REF["latitude"],
        "longitude": SAO_LUDGERO_REF["longitude"],
        "prioridade": 0,
        "observacao": "Consulta manual de diagnóstico.",
    }

    timeout = httpx.Timeout(15.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resultado = await _consultar_estacao_cemaden(
            client=client,
            estacao=estacao_debug,
            incluir_amostra=True,
        )

    return {
        "api": "Monitor São Ludgero API",
        "versao_integracao": "1.10-cemaden-diagnostico",
        "modo": "diagnostico",
        "mensagem": (
            "Endpoint de diagnóstico. Verifique status_http, campos_encontrados "
            "e dados_extraidos antes de usar em rotina operacional."
        ),
        "resultado": resultado,
    }


@app.get("/chuva-cemaden")
async def chuva_cemaden(
    max_idade_horas: int = Query(
        6,
        ge=1,
        le=168,
        description="Idade máxima aceitável do dado, em horas.",
    ),
    aceitar_dado_antigo: bool = Query(
        False,
        description="Se true, retorna a estação com dado válido mesmo fora da janela de idade.",
    ),
):
    """
    Consulta chuva observada do CEMADEN para São Ludgero e região.

    Estratégia:
    1. consulta estações candidatas próximas;
    2. identifica a estação mais próxima;
    3. seleciona a estação próxima com dado válido e recente;
    4. se não houver dado recente, retorna status 'sem_dado_recente'.
    """

    timeout = httpx.Timeout(15.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tarefas = [
            _consultar_estacao_cemaden(client, estacao, incluir_amostra=False)
            for estacao in CEMADEN_ESTACOES_CANDIDATAS
        ]
        resultados = await asyncio.gather(*tarefas)

    resultados_ordenados = sorted(
        resultados,
        key=lambda item: item["estacao"]["distancia_aproximada_km"],
    )

    estacao_mais_proxima = resultados_ordenados[0] if resultados_ordenados else None

    com_dado_valido = [
        r for r in resultados_ordenados
        if r.get("dados_validos") and r.get("dados_extraidos")
    ]

    recentes = []
    for r in com_dado_valido:
        idade = r["dados_extraidos"].get("idade_horas")
        if idade is not None and idade <= max_idade_horas:
            recentes.append(r)

    estacao_utilizada = None
    status = "sem_dado_recente"

    if recentes:
        estacao_utilizada = recentes[0]
        status = "ok"
    elif aceitar_dado_antigo and com_dado_valido:
        estacao_utilizada = com_dado_valido[0]
        status = "ok_dado_antigo"

    dados = estacao_utilizada.get("dados_extraidos") if estacao_utilizada else None

    return {
        "api": "Monitor São Ludgero API",
        "versao_integracao": "1.10-cemaden-diagnostico",
        "fonte": "CEMADEN - páginas públicas de gráficos de PCDs",
        "modo": "diagnostico",
        "status": status,
        "referencia": SAO_LUDGERO_REF,
        "janela_maxima_idade_horas": max_idade_horas,
        "estacao_mais_proxima": estacao_mais_proxima,
        "estacao_utilizada": estacao_utilizada,
        "chuva_observada_mm": dados.get("chuva_observada_mm") if dados else None,
        "acumulado_1h_mm": dados.get("acumulado_1h_mm") if dados else None,
        "acumulado_24h_mm": dados.get("acumulado_24h_mm") if dados else None,
        "acumulado_72h_mm": dados.get("acumulado_72h_mm") if dados else None,
        "datahora_utc": dados.get("datahora_utc_iso") if dados else None,
        "idade_horas": dados.get("idade_horas") if dados else None,
        "mensagem": (
            "Dado CEMADEN válido e dentro da janela operacional."
            if status == "ok"
            else "Nenhuma estação próxima retornou dado válido e recente dentro da janela definida."
        ),
        "estacoes_consultadas": resultados_ordenados,
    }
