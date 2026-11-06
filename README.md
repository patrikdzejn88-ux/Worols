# Worols

регистрация аккаунтов worols / account registration

файлы / files:
- autoreg.py — temptf + graphql registration + email activation
- proxies.example.txt — proxy list example
- requirements.txt

запуск / run:
```
pip install -r requirements.txt
python autoreg.py
python autoreg.py --count 20 --workers 5
python autoreg.py --count 10 --workers 3 --provider gmail
```

почты / mail providers (`all`, `gmail`, `gmail-dot`, `gmail-plus`, `high.edu.pl`) — выбираются в меню при запуске или через `--provider`

капчи нет, активация по ссылке из письма автоматическая

аккаунты падают в logs/accounts.json

прокси / proxies, один на строку:
```
http://user:pass@host:port
host:port:user:pass
host:port
```
sticky-сессия подставляется автоматически (-sessid-...-sesstime-15)
