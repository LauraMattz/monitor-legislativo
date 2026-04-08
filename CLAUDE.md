# Placar de Emendas Parlamentares

Pipeline de análise de emendas parlamentares cruzando com gastos da cota (CEAP) e perfil dos parlamentares.

## Pergunta central

Onde os parlamentares direcionam o dinheiro federal via emendas — e o quanto isso custa ao contribuinte via cota parlamentar?

## Fontes de dados (via mcp-brasil)

| Agente | Módulo | O que coleta |
|--------|--------|-------------|
| agente-camara | `mcp_brasil.data.camara` | Deputados, gastos da cota parlamentar (CEAP), PLs propostos |
| agente-senado | `mcp_brasil.data.senado` | Senadores, votações recentes |
| agente-emendas | `mcp_brasil.data.transferegov` | Emendas PIX por parlamentar, por UF, por município |
| agente-analista | — | Cruzamento, scores, rankings |
| agente-redator | — | HTML final |

## Métricas calculadas

1. **Ranking de emendas**: total em R$ por parlamentar (2024)
2. **Concentração geográfica**: % das emendas que volta para o estado que o elegeu
3. **Índice de retorno**: R$ em emendas / R$ gasto da cota parlamentar
4. **Foco educacional**: % de emendas direcionadas à área de educação
5. **Ranking de cota**: quem mais gasta da cota e em quê (categoria)
6. **Destinos das emendas**: top municípios beneficiados
7. **Análise por partido**: média de emendas e concentração geográfica por legenda

## Limites metodológicos

- Emendas PIX (transferegov) são um subconjunto das emendas totais. Emendas de comissão e emendas individuais alocadas via convênio não aparecem aqui.
- Cota parlamentar (CEAP) não inclui salário, auxílios ou benefícios — só gastos de escritório/atividade parlamentar.
- "Concentração geográfica" não é necessariamente ruim: deputado que direciona emendas para o estado que representa pode estar atendendo sua base eleitoral legitimamente.
- O cruzamento "cota vs emendas" mede escala relativa, não eficiência em sentido normativo.

## Estrutura de saída

`output/YYYY-MM-DD.html` — relatório com ranking, gráficos e destaques.

## Como rodar

```bash
python gerar_relatorio.py
```
