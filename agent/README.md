# Ozturk Print Agent

Restoran ichidagi chek printerlari uchun agent. Serverdan (`Ozturk Print Job`)
topshiriqlarni oladi va printerga (IP:9100, ESC/POS) yuboradi. Ulanish doim
restorandan serverga (HTTPS) — VPN kerak emas.

## Server tomonida (bir marta)

```bash
bench --site <site> migrate                      # DocType, rol, agent foydalanuvchisi
bench --site <site> execute ozturkapp.ozturkapp.setup.print_setup.issue_agent_credentials
```
Chiqqan `api_key` / `api_secret` ni `config.json` ga yozing.

ERPNext da **Ozturk Printer** ro'yxatini to'ldiring: Kassa (role=Kassa) va har
bir oshxona stansiyasi uchun (role=Oshxona, production_unit) IP manzillari bilan.

## Monoblokda (Windows 10/11)

1. `agent` papkasini monoblokka ko'chiring (masalan `C:\Users\gl-kassa\agent`).
2. `config.example.json` ni `config.json` qilib nusxalang va to'ldiring.
3. Administrator PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install_agent.ps1
   ```
   Agent `C:\OzturkPrintAgent` ga o'rnatiladi va Windows ishga tushganda
   avtomatik ishlaydi. Log: `C:\OzturkPrintAgent\agent.log`.

Tekshirish: ERPNext da Ozturk Printer → printerni oching → "Sinov cheki"
(yoki `api.printing.test_print`). Kassa sahifasida "Hisob berish" bosilganda
chek kassa printeridan chiqadi.

O'chirish: `install_agent.ps1 -Uninstall`.
