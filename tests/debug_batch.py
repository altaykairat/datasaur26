import pandas as pd
from database.connection import get_db
from engine.router import TicketRouter
import logging

logging.basicConfig(level=logging.INFO)

df = pd.DataFrame([{
    "GUID клиента": "b44f142b-test",
    "Сегмент клиента": "Mass",
    "Населённый пункт": "г. Шымкент",
    "Область": "Шымкент",
    "Описание ": "",
    "Улица": "пр. Республики",
    "Дом": "25",
    "Вложение": "order_error.png"
}])

router = TicketRouter(ai_mode="qwen")
batch_res = router.route_batch(df)

for idx, row in batch_res.iterrows():
    print("---------------------------------")
    for col in row.index:
        print(f"{col}: {row[col]}")
