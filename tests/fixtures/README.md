# Real TSE fixtures

These are directly downloaded public official files, not invented candidates or simulated votes. Captured on 2026-10-25.

- `ea11-live.json`: https://resultados.tse.jus.br/oficial/comum/config/ele-c.json
- `br-live.json`: https://resultados.tse.jus.br/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json ; generated 25/10/2026 17:06:31 BRT, 1,225 counted sections, 335,743 valid votes.
- `sp-live.json`: https://resultados.tse.jus.br/oficial/ele2026/6258/dados/sp/sp-c0001-e006258-u.json ; pre-count state, zero valid votes.

Election codes in these fixture source URLs record historical evidence. Production discovers codes from EA11; it never selects a live election from these fixtures. Tests mutate copies for explicit error and hypothetical arithmetic cases. The Postman mock embeds these real files and injects transport errors solely in local tests.

Additional official fixtures captured on 2026-10-25:

- `ea12-live.json`: https://resultados.tse.jus.br/oficial/ele2026/6258/config/mun-e006258-cm.json ; 5,757 municipal/overseas locations.
- `municipal-live.json`: https://resultados.tse.jus.br/oficial/ele2026/6258/dados/ac/ac01120-c0001-e006258-u.json ; Acrelandia, municipal scope.
- `first-round-br-live.json`: https://resultados.tse.jus.br/oficial/ele2026/6257/dados/br/br-c0001-e006257-u.json ; finalized first round, source generated 05/10/2026 12:51:47 BRT. Used as historical baseline only.

These captured election IDs are fixture provenance, not production configuration.
