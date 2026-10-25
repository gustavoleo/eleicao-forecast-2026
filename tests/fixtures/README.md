# Real TSE fixtures

These are directly downloaded public official files, not invented candidates or simulated votes. Captured on 2026-10-25.

- `ea11-live.json`: https://resultados.tse.jus.br/oficial/comum/config/ele-c.json
- `br-live.json`: https://resultados.tse.jus.br/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json ; generated 25/10/2026 17:06:31 BRT, 1,225 counted sections, 335,743 valid votes.
- `sp-live.json`: https://resultados.tse.jus.br/oficial/ele2026/6258/dados/sp/sp-c0001-e006258-u.json ; pre-count state, zero valid votes.

Election codes in these fixture source URLs record historical evidence. Production discovers codes from EA11; it never selects a live election from these fixtures. Tests mutate copies for explicit error and hypothetical arithmetic cases. The Postman mock embeds these real files and injects transport errors solely in local tests.
