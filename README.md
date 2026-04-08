# 💸 Monitor Legislativo — Placar de Emendas PIX

Rastreie para onde cada parlamentar federal mandou suas emendas PIX — cruzando dados de Câmara, Senado, TransfereGov e IBGE com um único script.

![Python](https://img.shields.io/badge/python-3.10+-blue) ![Dados](https://img.shields.io/badge/dados-públicos-green) ![mcp-brasil](https://img.shields.io/badge/powered%20by-mcp--brasil-orange)

## O que é isso?

Emendas PIX (transferências especiais, art. 166-A CF) são verbas federais que parlamentares transferem diretamente para municípios — sem licitação, sem convênio, sem projeto prévio. Cada deputado tem cota de ~R$ 18M/ano; cada senador, ~R$ 52M/ano.

Este projeto mapeia **para onde foi esse dinheiro**, calculando:

- 🏆 Ranking por volume total
- 🗺️ Concentração geográfica (% ao próprio estado)
- 📐 IDH médio dos destinos (redistributivo ou regressivo?)
- 🌿 % direcionado ao Norte e Nordeste
- 🎯 Comparativo por partido

O resultado é um HTML interativo com tabelas ordenáveis, busca por nome/partido/UF e gráficos.

## Como funciona

4 agentes correm em paralelo com `asyncio.gather`:

```
agente_deputados()   → API Câmara (513 deputados)
agente_senadores()   → API Senado (81 senadores)
agente_emendas()     → TransfereGov (27 UFs em paralelo)
agente_municipios()  → IBGE (5.289 municípios)
```

Os dados são cruzados via fuzzy match (difflib) e enriquecidos com IDH por UF (PNUD 2010).

## Requisitos

```bash
pip install httpx mcp-brasil
```

O mcp-brasil expõe 300+ ferramentas de 40+ bases públicas brasileiras como interface única. Saiba mais: [github.com/jxnxts/mcp-brasil](https://github.com/jxnxts/mcp-brasil)

## Como usar

```bash
# Ano atual (padrão: ano corrente)
python gerar_relatorio.py

# Ano específico
python gerar_relatorio.py 2024
python gerar_relatorio.py 2025
```

O relatório é gerado em `output/YYYY-MM-DD.html`.

## Estrutura

```
monitor-legislativo/
├── gerar_relatorio.py     # script principal
├── agents/                # prompts de referência dos agentes
│   ├── agente-camara.md
│   ├── agente-emendas.md
│   └── agente-analista.md
├── data/                  # cache local (gerado automaticamente)
│   ├── deputados.json
│   ├── senadores.json
│   ├── emendas-data.json
│   └── analise.json
└── output/                # HTMLs gerados
```

## Métricas calculadas

| Métrica | Descrição |
|---|---|
| `total_emendas` | Valor total empenhado no ano |
| `conc_home_state_pct` | % das emendas no próprio estado |
| `idh_destino_medio` | IDH médio ponderado dos estados destino |
| `delta_idh` | IDH origem − IDH destino (+ = redistributivo) |
| `pct_norte_nordeste` | % para regiões N e NE |

## Limitações

- Apenas emendas PIX (art. 166-A). Não inclui emendas via convênio, de comissão ou de bancada.
- Valores de empenho, não necessariamente liquidados.
- IDH por UF referência PNUD 2010 (última com cobertura total).
- Join por nome com fuzzy match — ~33 parlamentares sem correspondência por apelidos distintos.

## Fontes

- [TransfereGov](https://www.transferegov.gov.br)
- [API Câmara dos Deputados](https://dadosabertos.camara.leg.br)
- [API Senado Federal](https://legis.senado.leg.br/dadosabertos)
- [IBGE Localidades](https://servicodados.ibge.gov.br)
- [Atlas Brasil / PNUD](https://www.atlasbrasil.org.br)

---

Feito com [mcp-brasil](https://github.com/jxnxts/mcp-brasil) · dados 100% públicos
