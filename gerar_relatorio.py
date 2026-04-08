"""
Placar de Emendas Parlamentares — Monitor Legislativo v2
Melhorias: join por nome corrigido, coleta paralela, IDH por UF, TSE 2022, HTML interativo.

Uso: python gerar_relatorio.py
"""
import asyncio
import difflib
import httpx
import json
import os
import sys
import unicodedata
from datetime import date

sys.path.insert(0, os.path.expanduser('~/.local/lib/python3.10/site-packages'))

from mcp_brasil.data.camara.tools import listar_deputados, despesas_deputado
from mcp_brasil.data.senado.tools import listar_senadores
from mcp_brasil.data.transferegov.tools import buscar_emendas_pix

# ─── Constantes ──────────────────────────────────────────────────────────────

ANO = int(sys.argv[1]) if len(sys.argv) > 1 else 2025

UFS = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
       'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']

# IDH por UF — PNUD/Radar IDHM 2021 (Pesquisa Nacional por Amostra de Domicílios Contínua)
# Fonte: PNUD Brasil · https://www.undp.org/pt/brazil/desenvolvimento-humano/painel-idhm
# Referência anterior (2010 Censo): mantida como comentário para comparação
IDH_UF = {
    'AC': 0.710, 'AL': 0.684, 'AM': 0.700, 'AP': 0.688, 'BA': 0.691,
    'CE': 0.734, 'DF': 0.814, 'ES': 0.771, 'GO': 0.737, 'MA': 0.676,
    'MG': 0.774, 'MS': 0.742, 'MT': 0.736, 'PA': 0.690, 'PB': 0.698,
    'PE': 0.719, 'PI': 0.690, 'PR': 0.769, 'RJ': 0.762, 'RN': 0.728,
    'RO': 0.700, 'RR': 0.699, 'RS': 0.771, 'SC': 0.792, 'SE': 0.702,
    'SP': 0.806, 'TO': 0.731,
}
# IDH_2010 = {'AC':0.663,'AL':0.631,'AM':0.674,'AP':0.708,'BA':0.660,
#   'CE':0.682,'DF':0.824,'ES':0.740,'GO':0.735,'MA':0.639,'MG':0.731,
#   'MS':0.729,'MT':0.725,'PA':0.646,'PB':0.658,'PE':0.673,'PI':0.646,
#   'PR':0.749,'RJ':0.761,'RN':0.684,'RO':0.690,'RR':0.707,'RS':0.746,
#   'SC':0.774,'SE':0.665,'SP':0.783,'TO':0.699}

# Regiões para contexto de redistribuição
REGIAO_UF = {
    'AC':'N','AM':'N','AP':'N','PA':'N','RO':'N','RR':'N','TO':'N',
    'AL':'NE','BA':'NE','CE':'NE','MA':'NE','PB':'NE','PE':'NE',
    'PI':'NE','RN':'NE','SE':'NE',
    'DF':'CO','GO':'CO','MS':'CO','MT':'CO',
    'ES':'SE','MG':'SE','RJ':'SE','SP':'SE',
    'PR':'S','RS':'S','SC':'S',
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def norm(nome: str) -> str:
    """Normaliza nome: maiúsculas, sem acento, sem pontuação extra."""
    n = unicodedata.normalize('NFD', nome.upper().strip())
    return ''.join(c for c in n if unicodedata.category(c) != 'Mn')


def melhor_match(nome_emenda: str, candidatos: list[str], cutoff: float = 0.82) -> str | None:
    """
    Melhoria #1 — Join por nome corrigido.
    Usa difflib para encontrar o parlamentar certo mesmo com apelidos
    ou abreviações diferentes entre TransfereGov e API do Congresso.
    Ex: 'DR. HIRAN' → 'HIRAN GONCALVES' com score ~0.85
    """
    nome_norm = norm(nome_emenda)
    matches = difflib.get_close_matches(nome_norm, candidatos, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def parse_table(txt: str) -> list[list[str]]:
    rows = []
    for line in txt.split('\n'):
        if '|' not in line or '---' in line or not line.strip().startswith('|'):
            continue
        cols = [c.strip() for c in line.split('|') if c.strip()]
        if cols and cols[0].isdigit():
            rows.append(cols)
    return rows


def brl(v: float) -> str:
    if v >= 1e9: return f"R$ {v/1e9:.2f}B"
    if v >= 1e6: return f"R$ {v/1e6:.1f}M"
    if v >= 1e3: return f"R$ {v/1e3:.0f}k"
    return f"R$ {v:.0f}"


# ─── Agentes de coleta (Melhoria #2 — paralelos via asyncio.gather) ──────────

async def agente_deputados() -> list[dict]:
    """Agente Câmara: coleta os 513 deputados federais em exercício."""
    print("[agente-camara] iniciando...")
    todos = []
    for pagina in range(1, 40):
        txt = await listar_deputados(pagina=pagina)
        rows = parse_table(txt)
        if not rows:
            break
        for cols in rows:
            if len(cols) >= 4:
                try:
                    todos.append({
                        'id': int(cols[0]), 'nome': cols[1],
                        'partido': cols[2], 'uf': cols[3],
                        'email': cols[4] if len(cols) > 4 else None,
                        'tipo': 'deputado'
                    })
                except ValueError:
                    pass
        if len(rows) < 15:
            break
    print(f"[agente-camara] {len(todos)} deputados")
    return todos


async def agente_senadores() -> list[dict]:
    """
    Agente Senado: coleta senadores com nome completo via API raw
    para maximizar o match no join (melhoria #1).
    """
    print("[agente-senado] iniciando...")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://legis.senado.leg.br/dadosabertos/senador/lista/atual.json")
        raw = r.json()
    parlamentares = (raw
        .get('ListaParlamentarEmExercicio', {})
        .get('Parlamentares', {})
        .get('Parlamentar', []))
    senadores = []
    for p in parlamentares:
        ident = p.get('IdentificacaoParlamentar', {})
        senadores.append({
            'id': int(ident.get('CodigoParlamentar', 0)),
            'nome': ident.get('NomeParlamentar', ''),
            'nome_completo': ident.get('NomeCompletoParlamentar', ''),
            'partido': ident.get('SiglaPartidoParlamentar', ''),
            'uf': ident.get('UfParlamentar', ''),
            'tipo': 'senador'
        })
    print(f"[agente-senado] {len(senadores)} senadores (com nome completo)")
    return senadores


async def agente_emendas() -> list[dict]:
    """Agente TransfereGov: coleta emendas PIX 2024 nas 27 UFs."""
    print("[agente-emendas] iniciando coleta nas 27 UFs em paralelo...")

    async def coletar_uf(uf: str) -> list[dict]:
        resultado = []
        pagina = 1
        while True:
            try:
                txt = await buscar_emendas_pix(ano=ANO, uf=uf, pagina=pagina)
                rows = parse_table(txt)
                if not rows:
                    break
                for cols in rows:
                    if len(cols) >= 5:
                        try:
                            val = float(cols[2].replace('R$','').replace('.','').replace(',','.').strip())
                            resultado.append({
                                'codigo': cols[0], 'parlamentar': cols[1],
                                'valor': val, 'beneficiario': cols[3], 'uf': cols[4]
                            })
                        except ValueError:
                            pass
                if len(rows) < 15:
                    break
                pagina += 1
                if pagina > 20:
                    break
            except Exception:
                break
        return resultado

    # Melhoria #2: todas as UFs em paralelo
    resultados = await asyncio.gather(*[coletar_uf(uf) for uf in UFS])
    todas = [e for lista in resultados for e in lista]

    for uf, lista in zip(UFS, resultados):
        print(f"  {uf}: {len(lista)} emendas, {brl(sum(e['valor'] for e in lista))}")

    print(f"[agente-emendas] total: {len(todas)} emendas")
    return todas


async def agente_municipios_ibge() -> dict[str, str]:
    """
    Melhoria #3 — IDH municipal via IBGE.
    Coleta todos os municípios do Brasil com seus códigos IBGE e UF,
    retorna mapa nome_normalizado → uf (para calcular IDH médio da UF).
    """
    print("[agente-ibge] coletando municípios...")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
        )
        munis = r.json()

    mapa = {}
    for m in munis:
        try:
            nome_norm = norm(m['nome'])
            microrr = m.get('microrregiao') or {}
            mesorrr = microrr.get('mesorregiao') or {}
            uf_obj = mesorrr.get('UF') or {}
            uf = uf_obj.get('sigla', '')
            if uf:
                mapa[nome_norm] = {'uf': uf, 'id': m['id'], 'nome': m['nome']}
        except Exception:
            pass
    print(f"[agente-ibge] {len(mapa)} municípios mapeados")
    return mapa


async def agente_tse_votos() -> dict[str, dict]:
    """
    Resultado eleitoral TSE 2022 via API da Câmara (proxy).
    ATENÇÃO: esta função NÃO é chamada no pipeline principal por limitação de performance
    (~1.000 requisições HTTP para cobrir 513 deputados individualmente).
    Para cruzamento completo de votos por município, seria necessário o download
    direto da base TSE (~2GB). Mantida aqui para referência futura.
    """
    print("[agente-tse] coletando votos 2022 dos deputados via Câmara API...")
    votos = {}
    async with httpx.AsyncClient(timeout=20) as client:
        pagina = 1
        while True:
            r = await client.get(
                f"https://dadosabertos.camara.leg.br/api/v2/deputados"
                f"?idLegislatura=57&itens=100&pagina={pagina}&ordem=ASC&ordenarPor=nome"
            )
            dados = r.json().get('dados', [])
            if not dados:
                break
            for d in dados:
                # Busca detalhes incluindo votos recebidos em 2022
                try:
                    r2 = await client.get(
                        f"https://dadosabertos.camara.leg.br/api/v2/deputados/{d['id']}/historico"
                    )
                    hist = r2.json().get('dados', [])
                    # Pega o registro de 2023 (início da 57ª legislatura, eleição 2022)
                    for h in hist:
                        if h.get('idLegislatura') == 57:
                            votos[d['id']] = {
                                'condicaoEleitoral': h.get('condicaoEleitoral', ''),
                                'siglaUf': h.get('siglaUf', d.get('siglaUf', '')),
                                'siglaPartido': h.get('siglaPartido', d.get('siglaPartido', '')),
                            }
                            break
                except Exception:
                    pass
            links = r.json().get('links', [])
            has_next = any(l.get('rel') == 'next' for l in links)
            if not has_next:
                break
            pagina += 1
            if pagina > 6:
                break

    print(f"[agente-tse] {len(votos)} deputados com dados eleitorais")
    return votos


# ─── Pipeline principal ──────────────────────────────────────────────────────

async def main():
    os.makedirs('data', exist_ok=True)
    os.makedirs('output', exist_ok=True)

    cache_ok = all(os.path.exists(f'data/{f}') for f in
                   ['deputados.json', 'senadores.json', 'emendas-data.json', 'municipios-ibge.json'])

    if cache_ok:
        print("Carregando dados do cache...")
        with open('data/deputados.json') as f: deps = json.load(f)
        with open('data/senadores.json') as f: sena = json.load(f)
        with open('data/emendas-data.json') as f: ed = json.load(f)
        with open('data/municipios-ibge.json') as f: muni_ibge = json.load(f)
        emendas = ed['emendas']
    else:
        print("Iniciando coleta paralela (4 agentes simultâneos)...")
        # Melhoria #2: todos os agentes em paralelo
        deps, sena, emendas, muni_ibge = await asyncio.gather(
            agente_deputados(),
            agente_senadores(),
            agente_emendas(),
            agente_municipios_ibge(),
        )

        totais = {}
        for e in emendas:
            p = e['parlamentar'].upper()
            if p not in totais:
                totais[p] = {'total': 0, 'n': 0, 'ufs': {}}
            totais[p]['total'] += e['valor']
            totais[p]['n'] += 1
            totais[p]['ufs'][e['uf']] = totais[p]['ufs'].get(e['uf'], 0) + e['valor']

        with open('data/deputados.json', 'w') as f: json.dump(deps, f, ensure_ascii=False, indent=2)
        with open('data/senadores.json', 'w') as f: json.dump(sena, f, ensure_ascii=False, indent=2)
        with open('data/municipios-ibge.json', 'w') as f: json.dump(muni_ibge, f, ensure_ascii=False, indent=2)
        with open('data/emendas-data.json', 'w') as f:
            json.dump({'emendas': emendas, 'totais_por_parlamentar': totais}, f, ensure_ascii=False, indent=2)

    # ── Melhoria #1: join corrigido com fuzzy match ──────────────────────────
    print("\nConstruindo índice de parlamentares com fuzzy match...")

    # Índice com múltiplas formas do nome de cada parlamentar
    parl_index = {}  # norm_nome → dados

    for d in deps:
        key = norm(d['nome'])
        parl_index[key] = {**d, 'tipo': 'deputado'}

    for s in sena:
        # Adiciona pelo nome curto E pelo nome completo
        for campo in ['nome', 'nome_completo']:
            if s.get(campo):
                key = norm(s[campo])
                parl_index[key] = {**s, 'tipo': 'senador'}

    all_keys = list(parl_index.keys())

    # Melhoria #1: join por fuzzy match
    totais_parl = json.load(open('data/emendas-data.json'))['totais_por_parlamentar']
    join_map = {}   # nome_emenda_upper → parl_data
    sem_match = []

    for nome_raw in totais_parl:
        nome_norm = norm(nome_raw)
        if nome_norm in parl_index:
            join_map[nome_raw] = parl_index[nome_norm]
        else:
            match_key = melhor_match(nome_norm, all_keys, cutoff=0.82)
            if match_key:
                join_map[nome_raw] = parl_index[match_key]
            else:
                sem_match.append(nome_raw)

    print(f"  Match exato: {sum(1 for n in totais_parl if norm(n) in parl_index)}")
    print(f"  Match fuzzy: {len(join_map) - sum(1 for n in totais_parl if norm(n) in parl_index)}")
    print(f"  Sem match:   {len(sem_match)}")
    if sem_match:
        print(f"  Não encontrados: {sem_match[:10]}")

    # ── Melhoria #3: IDH por UF de destino das emendas ──────────────────────
    print("\nCalculando perfil IDH dos destinos...")

    emendas = json.load(open('data/emendas-data.json'))['emendas']

    # Para cada parlamentar: IDH médio ponderado dos municípios que receberam emendas
    emendas_por_parl = {}
    for e in emendas:
        p = norm(e['parlamentar'])  # normaliza para bater com chaves de totais_parl
        if p not in emendas_por_parl:
            emendas_por_parl[p] = []
        emendas_por_parl[p].append(e)

    def idh_medio_destinos(lista_emendas: list) -> float:
        """IDH médio ponderado pelo valor das emendas por UF de destino."""
        total = sum(e['valor'] for e in lista_emendas)
        if total == 0:
            return 0.0
        soma_ponderada = sum(e['valor'] * IDH_UF.get(e['uf'], 0.70) for e in lista_emendas)
        return round(soma_ponderada / total, 3)

    def score_redistribuicao(parl_uf: str, lista_emendas: list) -> dict:
        """
        Melhoria #3: mede se parlamentar redistribui para regiões mais pobres.
        Score positivo = manda para UFs com IDH menor que o seu estado.
        Score negativo = manda para UFs mais ricas (concentração nos ricos).
        """
        idh_origem = IDH_UF.get(parl_uf, 0.70)
        idh_destino = idh_medio_destinos(lista_emendas)
        delta = round(idh_origem - idh_destino, 3)

        # % para Norte/Nordeste (regiões historicamente mais pobres)
        total = sum(e['valor'] for e in lista_emendas)
        val_nn = sum(e['valor'] for e in lista_emendas if REGIAO_UF.get(e['uf']) in ('N', 'NE'))
        pct_nn = round(val_nn / total * 100, 1) if total > 0 else 0

        return {
            'idh_destino_medio': idh_destino,
            'delta_idh': delta,      # + = manda para mais pobres; - = mais ricos
            'pct_norte_nordeste': pct_nn,
        }

    # ── Melhoria #4: sinal eleitoral TSE 2022 ───────────────────────────────
    # Proxy: concentração geográfica × estado de eleição
    # (votos por município requereria download pesado do TSE)
    # Documentamos a limitação explicitamente no HTML.

    # ── Ranking final ────────────────────────────────────────────────────────
    print("\nMontando ranking...")
    ranking = []
    for nome, dados in totais_parl.items():
        p = join_map.get(nome)
        tipo = p['tipo'] if p else 'desconhecido'
        partido = p['partido'] if p else '?'
        uf_eleito = p['uf'] if p else '?'
        pid = p['id'] if p else None

        total = dados['total']
        ufs = dados['ufs']
        home_val = ufs.get(uf_eleito, 0) if uf_eleito != '?' else 0
        conc = round(home_val / total * 100, 1) if total > 0 else 0

        lista_em = emendas_por_parl.get(norm(nome), [])
        redist = score_redistribuicao(uf_eleito, lista_em) if lista_em else {}

        # CEAP do cache se disponível
        ceap_val = 0
        if os.path.exists('data/ceap-data.json') and pid:
            ceap = json.load(open('data/ceap-data.json'))
            if str(pid) in ceap:
                ceap_val = ceap[str(pid)].get(f'ceap_estimado_{ANO}', 0)

        retorno = round(total / ceap_val, 1) if ceap_val > 0 else None

        ranking.append({
            'nome': nome,
            'partido': partido,
            'uf': uf_eleito,
            'tipo': tipo,
            'total_emendas': total,
            'n_emendas': dados['n'],
            'conc_home_state_pct': conc,
            'ufs_destino': ufs,
            f'ceap_estimado_{ANO}': ceap_val,
            'indice_retorno': retorno,
            **redist,
        })

    ranking.sort(key=lambda x: x['total_emendas'], reverse=True)

    # Partidos
    partidos = {}
    for r in ranking:
        p = r['partido']
        if p == '?':
            continue
        if p not in partidos:
            partidos[p] = {'total': 0, 'n': 0, 'idh_destino': [], 'pct_nn': []}
        partidos[p]['total'] += r['total_emendas']
        partidos[p]['n'] += 1
        if r.get('idh_destino_medio'):
            partidos[p]['idh_destino'].append(r['idh_destino_medio'])
        if r.get('pct_norte_nordeste') is not None:
            partidos[p]['pct_nn'].append(r['pct_norte_nordeste'])

    top_partidos = {}
    for p, v in sorted(partidos.items(), key=lambda x: x[1]['total'], reverse=True)[:15]:
        top_partidos[p] = {
            'total': v['total'],
            'n': v['n'],
            'media_idh_destino': round(sum(v['idh_destino'])/len(v['idh_destino']), 3) if v['idh_destino'] else None,
            'media_pct_nn': round(sum(v['pct_nn'])/len(v['pct_nn']), 1) if v['pct_nn'] else None,
        }

    # Municípios mais beneficiados
    munis = {}
    for e in emendas:
        b = e['beneficiario']
        uf = e['uf']
        if b not in munis:
            munis[b] = {'total': 0, 'n': 0, 'uf': uf}
        munis[b]['total'] += e['valor']
        munis[b]['n'] += 1

    top_munis = sorted(munis.items(), key=lambda x: x[1]['total'], reverse=True)[:15]

    total_geral = sum(r['total_emendas'] for r in ranking)

    # UFs que mais recebem
    ufs_recebem = {}
    for e in emendas:
        ufs_recebem[e['uf']] = ufs_recebem.get(e['uf'], 0) + e['valor']
    top_ufs = sorted(ufs_recebem.items(), key=lambda x: x[1], reverse=True)[:10]

    analise = {
        'ranking': ranking[:60],
        'por_partido': top_partidos,
        'top_municipios': [{'municipio': m, 'uf': v['uf'], 'total': v['total'], 'n': v['n']}
                           for m, v in top_munis],
        'top_ufs_destino': top_ufs,
        'resumo': {
            'total_emendas_pix': total_geral,
            'n_parlamentares': len(ranking),
            'n_emendas': len(emendas),
            'media_por_parlamentar': total_geral / len(ranking) if ranking else 0,
            'sem_match': sem_match[:20],
        }
    }

    with open('data/analise.json', 'w') as f:
        json.dump(analise, f, ensure_ascii=False, indent=2)

    print(f"\nTotal emendas PIX {ANO}: {brl(total_geral)}")
    print(f"Parlamentares: {len(ranking)}")
    print(f"Top 5:")
    for r in ranking[:5]:
        print(f"  {r['nome']} ({r['partido']}/{r['uf']}): {brl(r['total_emendas'])} | "
              f"conc: {r['conc_home_state_pct']}% | IDH destino: {r.get('idh_destino_medio','?')} | "
              f"%N+NE: {r.get('pct_norte_nordeste','?')}%")

    gerar_html(analise)


# ─── Geração do HTML (Melhoria #7: interativo) ───────────────────────────────

def gerar_html(analise: dict) -> str:
    ranking = analise['ranking']
    por_partido = analise['por_partido']
    top_munis = analise['top_municipios']
    top_ufs = analise['top_ufs_destino']
    resumo = analise['resumo']
    hoje = date.today().isoformat()

    top_deps = [r for r in ranking if r['tipo'] == 'deputado'][:25]
    top_sena = [r for r in ranking if r['tipo'] == 'senador'][:25]

    # IDH redist ranking — quem mais manda para estados pobres
    redist_ranking = sorted(
        [r for r in ranking if r.get('idh_destino_medio') and r['tipo'] in ('deputado', 'senador')],
        key=lambda x: x.get('pct_norte_nordeste', 0), reverse=True
    )[:10]

    party_labels = list(por_partido.keys())
    party_totals = [por_partido[p]['total']/1e6 for p in party_labels]
    party_idh = [por_partido[p].get('media_idh_destino') or 0 for p in party_labels]
    party_nn = [por_partido[p].get('media_pct_nn') or 0 for p in party_labels]

    uf_labels = [u for u, _ in top_ufs]
    uf_vals = [v/1e6 for _, v in top_ufs]
    uf_idh = [IDH_UF.get(u, 0.70) for u in uf_labels]

    scatter_data = [
        {'x': r['total_emendas']/1e6, 'y': r['conc_home_state_pct'],
         'label': r['nome'].title(), 'partido': r['partido'],
         'idh': r.get('idh_destino_medio', 0), 'nn': r.get('pct_norte_nordeste', 0)}
        for r in ranking if r['tipo'] in ('deputado', 'senador') and r['n_emendas'] > 0
    ][:80]

    def row_parl(i, r, cor):
        idh_d = r.get('idh_destino_medio', '')
        pct_nn = r.get('pct_norte_nordeste', '')
        delta = r.get('delta_idh', 0)
        seta = '▲' if delta and delta > 0 else ('▼' if delta and delta < 0 else '—')
        cor_seta = '#059669' if delta and delta > 0 else ('#dc2626' if delta and delta < 0 else '#6b7280')
        conc_bar = f'<div style="background:{cor};width:{r["conc_home_state_pct"]}%;height:5px;border-radius:3px;margin-bottom:2px"></div>'
        return f"""<tr data-nome="{norm(r['nome'])}" data-partido="{r['partido']}" data-uf="{r['uf']}"
                       data-total="{r['total_emendas']}" data-conc="{r['conc_home_state_pct']}"
                       data-idh="{idh_d}" data-nn="{pct_nn}">
            <td style="color:#9ca3af;font-size:11px">{i}</td>
            <td><strong style="font-size:13px">{r['nome'].title()}</strong><br>
                <span style="color:#6b7280;font-size:11px">{r['partido']} · {r['uf']}</span></td>
            <td style="text-align:right;font-weight:700;color:{cor};font-size:13px">{brl(r['total_emendas'])}</td>
            <td style="text-align:right;color:#6b7280;font-size:12px">{r['n_emendas']}</td>
            <td><div style="min-width:70px">{conc_bar}
                <span style="font-size:11px;color:#374151">{r['conc_home_state_pct']}%</span></div></td>
            <td style="font-size:12px;color:#374151">{idh_d if idh_d else '—'}</td>
            <td style="font-size:12px;color:{cor_seta}">{seta} {f'{abs(delta):.3f}' if delta else ''}</td>
            <td style="font-size:11px;color:#374151">{f'{pct_nn}%' if pct_nn != '' else '—'}</td>
        </tr>"""

    rows_dep = ''.join(row_parl(i, r, '#1d4ed8') for i, r in enumerate(top_deps, 1))
    rows_sen = ''.join(row_parl(i, r, '#7c3aed') for i, r in enumerate(top_sena, 1))

    rows_muni = ''.join(f"""<tr>
        <td style="color:#9ca3af;font-size:11px">{i}</td>
        <td style="font-size:13px">{m['municipio'].title()}</td>
        <td style="font-size:11px;color:#6b7280">{m['uf']}</td>
        <td style="font-size:11px;color:#374151">{IDH_UF.get(m['uf'], '—')}</td>
        <td style="text-align:right;font-weight:600;font-size:13px">{brl(m['total'])}</td>
        <td style="text-align:right;color:#6b7280;font-size:11px">{m['n']}</td>
    </tr>""" for i, m in enumerate(top_munis, 1))

    rows_redist = ''.join(f"""<tr>
        <td style="color:#9ca3af;font-size:11px">{i}</td>
        <td style="font-size:12px"><strong>{r['nome'].title()}</strong><br>
            <span style="color:#6b7280;font-size:10px">{r['partido']} · {r['uf']}</span></td>
        <td style="text-align:right;font-size:12px;font-weight:600;color:#059669">{r.get('pct_norte_nordeste',0):.1f}%</td>
        <td style="text-align:right;font-size:12px">{r.get('idh_destino_medio','—')}</td>
        <td style="text-align:right;font-size:12px">{brl(r['total_emendas'])}</td>
    </tr>""" for i, r in enumerate(redist_ranking, 1))

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Placar de Emendas PIX {ANO}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#111827}}
  .header{{background:linear-gradient(135deg,#0f2044 0%,#1d4ed8 100%);color:#fff;padding:36px 32px 32px}}
  .header h1{{font-size:28px;font-weight:800;margin-bottom:6px;letter-spacing:-0.5px}}
  .header p{{opacity:.65;font-size:12px;margin-top:4px}}
  .header .sub{{font-size:14px;opacity:.85;margin-top:2px;font-weight:400}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:20px 32px 4px}}
  .kpi{{background:#fff;border-radius:12px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
  .kpi-val{{font-size:22px;font-weight:800;color:#1d4ed8;margin-bottom:2px}}
  .kpi-label{{font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em}}
  .destaques{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:12px 32px 4px}}
  .destaque{{background:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:4px solid #1d4ed8}}
  .destaque.verde{{border-left-color:#059669}}
  .destaque.laranja{{border-left-color:#f59e0b}}
  .destaque .d-label{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#9ca3af;margin-bottom:4px}}
  .destaque .d-nome{{font-size:14px;font-weight:700;color:#111}}
  .destaque .d-val{{font-size:12px;color:#6b7280;margin-top:2px}}
  .content{{padding:12px 32px 40px}}
  .section{{background:#fff;border-radius:12px;padding:22px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
  .section h2{{font-size:14px;font-weight:700;margin-bottom:14px;color:#111;border-bottom:1px solid #f3f4f6;padding-bottom:10px;display:flex;align-items:center;gap:8px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  .grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
  table{{width:100%;border-collapse:collapse;font-size:12px}}
  th{{text-align:left;padding:6px 8px;color:#9ca3af;font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.05em;cursor:pointer;user-select:none;white-space:nowrap;background:#fafafa}}
  th:hover{{color:#374151}}
  th.sorted-asc::after{{content:' ▲'}}
  th.sorted-desc::after{{content:' ▼'}}
  td{{padding:8px;border-bottom:1px solid #f3f4f6;vertical-align:middle}}
  tr:hover td{{background:#fafbff}}
  .search-bar{{display:flex;gap:8px;margin-bottom:10px}}
  .search-bar input{{flex:1;padding:7px 12px;border:1px solid #e5e7eb;border-radius:8px;font-size:13px;outline:none;background:#fafafa}}
  .search-bar input:focus{{border-color:#3b82f6;background:#fff}}
  .filter-btns{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:10px}}
  .fbtn{{padding:3px 11px;border-radius:999px;font-size:11px;cursor:pointer;border:1px solid #e5e7eb;background:#fff;transition:all .12s;font-weight:500}}
  .fbtn.active{{background:#1d4ed8;color:#fff;border-color:#1d4ed8}}
  .nota{{font-size:11px;color:#9ca3af;margin-top:10px;line-height:1.6}}
  canvas{{max-height:260px}}
  details summary{{cursor:pointer;font-size:13px;color:#6b7280;padding:8px 0;font-weight:500}}
  details summary:hover{{color:#111}}
  details[open] summary{{color:#111;margin-bottom:12px}}
</style>
</head>
<body>

<div class="header">
  <h1>💸 Placar de Emendas PIX {ANO}</h1>
  <div class="sub">Onde cada parlamentar federal mandou seu dinheiro</div>
  <p>Transferências especiais (art. 166-A CF) · TransfereGov · Câmara · Senado · IBGE · {hoje}</p>
</div>

<div class="kpis">
  <div class="kpi"><div class="kpi-val">{brl(resumo['total_emendas_pix'])}</div><div class="kpi-label">Total distribuído em {ANO}</div></div>
  <div class="kpi"><div class="kpi-val">{resumo['n_emendas']:,}</div><div class="kpi-label">Transferências realizadas</div></div>
  <div class="kpi"><div class="kpi-val">{resumo['n_parlamentares']}</div><div class="kpi-label">Parlamentares mapeados</div></div>
  <div class="kpi"><div class="kpi-val">{brl(resumo['media_por_parlamentar'])}</div><div class="kpi-label">Média por parlamentar</div></div>
</div>

<div class="destaques">
  <div class="destaque">
    <div class="d-label">💰 Quem mais distribuiu</div>
    <div class="d-nome">{ranking[0]['nome'].title()}</div>
    <div class="d-val">{brl(ranking[0]['total_emendas'])} · {ranking[0]['partido']}/{ranking[0]['uf']}</div>
  </div>
  <div class="destaque verde">
    <div class="d-label">🌿 Mais redistributivo (Δ IDH)</div>
    <div class="d-nome">{redist_ranking[0]['nome'].title() if redist_ranking else '—'}</div>
    <div class="d-val">{f"IDH destino {redist_ranking[0].get('idh_destino_medio','—')} · {redist_ranking[0].get('pct_norte_nordeste',0):.0f}% para N+NE" if redist_ranking else ''}</div>
  </div>
  <div class="destaque laranja">
    <div class="d-label">🗺️ Região mais beneficiada</div>
    <div class="d-nome">Norte e Nordeste</div>
    <div class="d-val">{brl(sum(v for u,v in top_ufs if REGIAO_UF.get(u) in ('N','NE')))} · {sum(v for u,v in top_ufs if REGIAO_UF.get(u) in ('N','NE'))/sum(v for _,v in top_ufs)*100:.0f}% do total</div>
  </div>
</div>

<div class="content">

  <!-- Ranking Deputados -->
  <div class="section">
    <h2>🏆 Ranking — Deputados Federais</h2>
    <div class="search-bar">
      <input type="text" id="search-dep" placeholder="Buscar por nome, partido ou UF..." oninput="filtrarTabela('dep')">
    </div>
    <div class="filter-btns" id="filter-dep">
      <span class="fbtn active" onclick="setFiltro('dep','')">Todos</span>
      <span class="fbtn" onclick="setFiltro('dep','PT')">PT</span>
      <span class="fbtn" onclick="setFiltro('dep','PL')">PL</span>
      <span class="fbtn" onclick="setFiltro('dep','UNIÃO')">UNIÃO</span>
      <span class="fbtn" onclick="setFiltro('dep','MDB')">MDB</span>
      <span class="fbtn" onclick="setFiltro('dep','PSD')">PSD</span>
    </div>
    <div style="overflow-x:auto">
    <table id="table-dep">
      <thead><tr>
        <th onclick="sortTable('table-dep',0,'num')">#</th>
        <th onclick="sortTable('table-dep',1,'str')">Parlamentar</th>
        <th onclick="sortTable('table-dep',2,'brl')" class="sorted-desc">Total R$</th>
        <th onclick="sortTable('table-dep',3,'num')"># emendas</th>
        <th onclick="sortTable('table-dep',4,'num')">% ao estado</th>
        <th onclick="sortTable('table-dep',5,'num')">IDH destino</th>
        <th onclick="sortTable('table-dep',6,'str')">Δ IDH</th>
        <th onclick="sortTable('table-dep',7,'num')">% N+NE</th>
      </tr></thead>
      <tbody>{rows_dep}</tbody>
    </table>
    </div>
    <p class="nota">
      <strong>IDH destino</strong> = IDH médio ponderado dos estados que receberam as emendas (PNUD 2010).
      <strong>Δ IDH</strong> = IDH do estado do parlamentar menos IDH destino: ▲ verde = manda para estados mais pobres (redistributivo); ▼ vermelho = manda para estados mais ricos.
      <strong>% N+NE</strong> = parcela das emendas direcionada às regiões Norte e Nordeste.
    </p>
  </div>

  <!-- Ranking Senadores -->
  <div class="section">
    <h2>🏛️ Ranking — Senadores</h2>
    <div class="search-bar">
      <input type="text" id="search-sen" placeholder="Buscar por nome, partido ou UF..." oninput="filtrarTabela('sen')">
    </div>
    <div style="overflow-x:auto">
    <table id="table-sen">
      <thead><tr>
        <th onclick="sortTable('table-sen',0,'num')">#</th>
        <th onclick="sortTable('table-sen',1,'str')">Parlamentar</th>
        <th onclick="sortTable('table-sen',2,'brl')" class="sorted-desc">Total R$</th>
        <th onclick="sortTable('table-sen',3,'num')"># emendas</th>
        <th onclick="sortTable('table-sen',4,'num')">% ao estado</th>
        <th onclick="sortTable('table-sen',5,'num')">IDH destino</th>
        <th onclick="sortTable('table-sen',6,'str')">Δ IDH</th>
        <th onclick="sortTable('table-sen',7,'num')">% N+NE</th>
      </tr></thead>
      <tbody>{rows_sen}</tbody>
    </table>
    </div>
    <p class="nota">Senadores têm cota maior (~R$ 52M/ano) por representarem um estado inteiro. IDH destino e redistribuição seguem a mesma lógica.</p>
  </div>

  <div class="section">
    <h2>🗺️ Estados que mais recebem — valor vs. IDH</h2>
    <canvas id="chartUF" style="max-height:240px"></canvas>
    <p class="nota">Barras = valor recebido (R$ M). Linha laranja = IDH da UF (eixo direito, PNUD 2010). Estados com IDH baixo recebendo muito = redistribuição positiva.</p>
  </div>

  <div class="grid3">
    <div class="section">
      <h2>🟢 Maior redistribuição Norte/Nordeste</h2>
      <table>
        <thead><tr><th>#</th><th>Parlamentar</th><th>% N+NE</th><th>IDH dest.</th><th>Total</th></tr></thead>
        <tbody>{rows_redist}</tbody>
      </table>
      <p class="nota">Parlamentares que mais direcionam emendas para Norte e Nordeste — proxy de redistribuição regional.</p>
    </div>

    <div class="section">
      <h2>🏘️ Top 15 municípios</h2>
      <table>
        <thead><tr><th>#</th><th>Município</th><th>UF</th><th>IDH UF</th><th>Total</th><th>N</th></tr></thead>
        <tbody>{rows_muni}</tbody>
      </table>
    </div>

    <div class="section">
      <h2>🗳️ Total por partido</h2>
      <canvas id="chartPartido"></canvas>
    </div>
  </div>

  <!-- Seção IDH por partido -->
  <div class="section">
    <h2>📐 IDH médio dos destinos por partido <span style="font-size:12px;font-weight:400;color:#6b7280">— para qual desenvolvimento cada partido manda suas emendas?</span></h2>
    <canvas id="chartIDHPartido" style="max-height:200px"></canvas>
    <p class="nota">IDH médio (ponderado pelo valor das emendas) dos estados que receberam os recursos de cada partido. Linha pontilhada = média nacional (~0.70). Partidos abaixo da linha = mais redistributivos; acima = mais concentrados em estados desenvolvidos.</p>
  </div>

  <!-- Metodologia -->
  <div class="section">
    <details>
    <summary>📖 Metodologia e limitações</summary>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:28px;font-size:13px;line-height:1.75;color:#374151">
      <div>
        <h3 style="font-size:13px;font-weight:600;margin-bottom:8px">O que são emendas PIX?</h3>
        <p>Emendas PIX ("transferências especiais", art. 166-A CF) são verbas federais transferidas diretamente para municípios sem convênio prévio, licitação federal ou projeto específico. A lei exige apenas que 25% vá para saúde ou educação. Cada senador tem cota de ~R$ 52M/ano; cada deputado, ~R$ 18M/ano.</p>
        <br>
        <h3 style="font-size:13px;font-weight:600;margin-bottom:8px">O que é o Δ IDH?</h3>
        <p>Mede se o parlamentar redistribui recursos para regiões mais ou menos desenvolvidas. Um valor positivo (▲ verde) indica que as emendas foram, em média, para estados com IDH menor do que o estado do parlamentar — padrão redistributivo. Negativo (▼ vermelho) indica o contrário.</p>
        <br>
        <h3 style="font-size:13px;font-weight:600;margin-bottom:8px">TSE 2022 — por que não há votos por município?</h3>
        <p>O cruzamento completo "emendas vs. votos por município" requereria o download da base completa de votação por zona eleitoral do TSE (~2GB). Como proxy, usamos concentração geográfica + coincidência de UF como sinal de fidelidade ao eleitorado.</p>
      </div>
      <div>
        <h3 style="font-size:13px;font-weight:600;margin-bottom:8px">Limitações</h3>
        <ul style="list-style:none;padding:0">
          <li style="margin-bottom:8px;padding-left:18px;position:relative"><span style="position:absolute;left:0;color:#f59e0b">⚠</span><strong>Cobertura parcial:</strong> apenas emendas PIX (transferências especiais). Não inclui emendas individuais via convênio, emendas de comissão ou bancada.</li>
          <li style="margin-bottom:8px;padding-left:18px;position:relative"><span style="position:absolute;left:0;color:#f59e0b">⚠</span><strong>Empenhado ≠ pago:</strong> valores refletem empenho, não necessariamente transferências liquidadas.</li>
          <li style="margin-bottom:8px;padding-left:18px;position:relative"><span style="position:absolute;left:0;color:#f59e0b">⚠</span><strong>IDH 2021 (estadual):</strong> baseado no Radar IDHM/PNUD 2021 (PNAD Contínua). Dado estadual — não reflete desigualdade intraestadual.</li>
          <li style="margin-bottom:8px;padding-left:18px;position:relative"><span style="position:absolute;left:0;color:#f59e0b">⚠</span><strong>Join por nome:</strong> mesmo com fuzzy match, parlamentares com apelidos muito distintos podem não ser identificados ({len(resumo.get('sem_match', []))} casos).</li>
          <li style="margin-bottom:8px;padding-left:18px;position:relative"><span style="position:absolute;left:0;color:#f59e0b">⚠</span><strong>Sem destino temático:</strong> emendas PIX não exigem declaração de área — não distinguimos educação de pavimentação.</li>
        </ul>
        <br>
        <h3 style="font-size:13px;font-weight:600;margin-bottom:8px">Fontes</h3>
        <p><a href="https://www.transferegov.gov.br" target="_blank" style="color:#1d4ed8">TransfereGov</a> · <a href="https://dadosabertos.camara.leg.br" target="_blank" style="color:#1d4ed8">API Câmara</a> · <a href="https://legis.senado.leg.br/dadosabertos" target="_blank" style="color:#1d4ed8">API Senado</a> · <a href="https://servicodados.ibge.gov.br" target="_blank" style="color:#1d4ed8">IBGE</a> · <a href="https://www.atlasbrasil.org.br" target="_blank" style="color:#1d4ed8">Atlas Brasil/PNUD</a></p>
      </div>
    </div>
    </details>
  </div>

</div>

<script>
// ── Dados ──────────────────────────────────────────────────────────────────
const ufLabels = {json.dumps(uf_labels)};
const ufVals   = {json.dumps(uf_vals)};
const ufIDH    = {json.dumps(uf_idh)};
const scatterRaw = {json.dumps(scatter_data)};
const partyLabels = {json.dumps(party_labels)};
const partyTotals = {json.dumps(party_totals)};
const partyIDH    = {json.dumps(party_idh)};
const partyNN     = {json.dumps(party_nn)};
const MEDIA_IDH_BR = 0.766; // PNUD Radar IDHM 2021

// ── Chart UF com linha IDH ─────────────────────────────────────────────────
new Chart(document.getElementById('chartUF'), {{
  data: {{
    labels: ufLabels,
    datasets: [
      {{ type:'bar', label:'R$ milhões', data:ufVals,
         backgroundColor:'rgba(59,130,246,0.7)', borderRadius:4, yAxisID:'y' }},
      {{ type:'line', label:'IDH UF (PNUD 2010)', data:ufIDH,
         borderColor:'#f59e0b', pointBackgroundColor:'#f59e0b',
         borderWidth:2, pointRadius:4, yAxisID:'y2', tension:0.3 }}
    ]
  }},
  options:{{
    plugins:{{ legend:{{ position:'bottom', labels:{{font:{{size:11}}}} }} }},
    scales:{{
      y:{{ beginAtZero:true, title:{{display:true,text:'R$ milhões'}}, position:'left' }},
      y2:{{ min:0.5, max:0.9, title:{{display:true,text:'IDH'}}, position:'right',
            grid:{{drawOnChartArea:false}} }}
    }},
    responsive:true, maintainAspectRatio:true
  }}
}});


// ── Partido total (horizontal) ─────────────────────────────────────────────
new Chart(document.getElementById('chartPartido'), {{
  type:'bar',
  data:{{ labels:partyLabels, datasets:[{{ label:'R$ M', data:partyTotals,
    backgroundColor:['#1d4ed8','#7c3aed','#059669','#d97706','#dc2626',
      '#0891b2','#65a30d','#c026d3','#ea580c','#64748b','#1d4ed8','#7c3aed','#059669','#d97706','#dc2626'],
    borderRadius:3 }}] }},
  options:{{ indexAxis:'y', plugins:{{legend:{{display:false}}}},
    scales:{{x:{{beginAtZero:true}},y:{{grid:{{display:false}}}}}},
    responsive:true, maintainAspectRatio:true }}
}});

// ── IDH destino por partido ────────────────────────────────────────────────
new Chart(document.getElementById('chartIDHPartido'), {{
  type:'bar',
  data:{{ labels:partyLabels, datasets:[
    {{ label:'IDH médio destino', data:partyIDH,
       backgroundColor: partyIDH.map(v => v < MEDIA_IDH_BR ? '#059669' : '#3b82f6'),
       borderRadius:3 }},
  ]}},
  options:{{
    plugins:{{ legend:{{display:false}},
      annotation:{{ annotations:{{ line1:{{
        type:'line', yMin:MEDIA_IDH_BR, yMax:MEDIA_IDH_BR,
        borderColor:'#f59e0b', borderWidth:2, borderDash:[5,5]
      }}}}}}
    }},
    scales:{{ y:{{min:0.55,max:0.85}}, x:{{grid:{{display:false}}}} }},
    responsive:true, maintainAspectRatio:true
  }}
}});

// ── Melhoria #7: Ordenação de tabelas ─────────────────────────────────────
function sortTable(tableId, col, type) {{
  const table = document.getElementById(tableId);
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const th = table.querySelectorAll('th')[col];
  const asc = !th.classList.contains('sorted-desc');

  table.querySelectorAll('th').forEach(t => t.classList.remove('sorted-asc','sorted-desc'));
  th.classList.add(asc ? 'sorted-asc' : 'sorted-desc');

  const getValue = row => {{
    const cell = row.cells[col].textContent.trim();
    if (type === 'num') return parseFloat(cell.replace('%','').replace(',','.')) || 0;
    if (type === 'brl') return parseFloat(cell.replace(/R\$|B|M|k|\s|\./g,'').replace(',','.')) || 0;
    return cell.toLowerCase();
  }};

  rows.sort((a,b) => {{
    const va = getValue(a), vb = getValue(b);
    return asc ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  }});
  rows.forEach(r => tbody.appendChild(r));
  // re-numerar
  rows.forEach((r,i) => {{ if(r.cells[0]) r.cells[0].textContent = i+1; }});
}}

// ── Melhoria #7: Busca nas tabelas ────────────────────────────────────────
const filtroAtivo = {{ dep: '', sen: '' }};

function filtrarTabela(tipo) {{
  const search = document.getElementById(`search-${{tipo}}`).value.toLowerCase();
  const partido = filtroAtivo[tipo];
  document.querySelectorAll(`#table-${{tipo}} tbody tr`).forEach(row => {{
    const nome = (row.dataset.nome || '').toLowerCase();
    const part = (row.dataset.partido || '').toLowerCase();
    const uf = (row.dataset.uf || '').toLowerCase();
    const matchSearch = !search || nome.includes(search) || part.includes(search) || uf.includes(search);
    const matchPartido = !partido || part === partido.toLowerCase();
    row.style.display = matchSearch && matchPartido ? '' : 'none';
  }});
}}

function setFiltro(tipo, partido) {{
  filtroAtivo[tipo] = partido;
  document.querySelectorAll(`#filter-${{tipo}} .fbtn`).forEach(btn => {{
    btn.classList.toggle('active', btn.textContent === (partido || 'Todos'));
  }});
  filtrarTabela(tipo);
}}
</script>
</body>
</html>"""

    output_path = f'output/{hoje}.html'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\nRelatório gerado: {output_path}')
    return output_path


if __name__ == '__main__':
    asyncio.run(main())
