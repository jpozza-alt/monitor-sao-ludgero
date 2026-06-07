def buscar_alertas_inmet():
    url_base = "https://apiprevmet3.inmet.gov.br/avisos/rss"

    resposta = requests.get(url_base, timeout=20)
    resposta.raise_for_status()

    texto = resposta.text

    ids = sorted(set(re.findall(r"avisos/(?:rss/)?(\d+)", texto)))

    alertas = []

    agora = datetime.now().astimezone()

    for aviso_id in ids[:100]:

        try:
            url_aviso = f"https://apiprevmet3.inmet.gov.br/avisos/rss/{aviso_id}"

            r = requests.get(url_aviso, timeout=15)

            if r.status_code != 200:
                continue

            raiz = ET.fromstring(r.content)

            headline = texto_xml(raiz, "headline")
            description = texto_xml(raiz, "description")
            severity = texto_xml(raiz, "severity")
            urgency = texto_xml(raiz, "urgency")
            certainty = texto_xml(raiz, "certainty")
            onset = texto_xml(raiz, "onset")
            expires = texto_xml(raiz, "expires")
            instruction = texto_xml(raiz, "instruction")
            area_desc = texto_xml(raiz, "areaDesc") or ""

            pertence_regiao = any(
                termo.lower() in area_desc.lower()
                for termo in TERMOS_REGIAO_SC
            )

            if not pertence_regiao:
                continue

            try:
                data_fim = datetime.fromisoformat(expires)

                if data_fim < agora:
                    continue

            except Exception:
                continue

            alertas.append({
                "id": aviso_id,
                "titulo": headline,
                "descricao": description,
                "severidade": severity,
                "urgencia": urgency,
                "certeza": certainty,
                "inicio": onset,
                "fim": expires,
                "areas_afetadas": area_desc,
                "instrucoes": instruction,
                "link": f"https://avisos.inmet.gov.br/{aviso_id}"
            })

        except Exception:
            continue

    alertas.sort(
        key=lambda x: x["inicio"]
    )

    return alertas
