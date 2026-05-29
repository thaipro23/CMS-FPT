# Test trên máy Windows đã mount code `E:\FPL\openedx-platform`

## 1. Giải nén

Giải nén zip này vào một thư mục tạm, sau đó copy 2 thư mục sau vào root source:

```text
E:\FPL\openedx-platform\openedx_unit_reset
E:\FPL\openedx-platform\tutor-plugins
```

Đúng cấu trúc phải là:

```text
E:\FPL\openedx-platform\openedx_unit_reset\setup.py
E:\FPL\openedx-platform\openedx_unit_reset\openedx_unit_reset\apps.py
E:\FPL\openedx-platform\openedx_unit_reset\openedx_unit_reset\settings\common.py
E:\FPL\openedx-platform\tutor-plugins\openedx_unit_reset.py
```

Không để thành:

```text
E:\FPL\openedx-platform\openedx_unit_reset_secure_full_v4\openedx_unit_reset\...
```

## 2. Copy Tutor plugin

CMD Windows:

```bat
cd /d E:\FPL\openedx-platform
for /f "delims=" %i in ('tutor plugins printroot') do copy /Y "E:\FPL\openedx-platform\tutor-plugins\openedx_unit_reset.py" "%i\openedx_unit_reset.py"
tutor plugins enable openedx_unit_reset
tutor config save
```

PowerShell:

```powershell
cd E:\FPL\openedx-platform
$pluginRoot = tutor plugins printroot
Copy-Item "E:\FPL\openedx-platform\tutor-plugins\openedx_unit_reset.py" "$pluginRoot\openedx_unit_reset.py" -Force
tutor plugins enable openedx_unit_reset
tutor config save
```

## 3. Build

```bat
tutor images build openedx --no-cache
```

## 4. Start và migrate

```bat
tutor local stop
tutor local start -d
docker exec -it tutor_local-lms-1 bash -lc "cd /openedx/edx-platform && python manage.py lms migrate openedx_unit_reset --settings=tutor.production"
docker exec -it tutor_local-lms-1 bash -lc "cd /openedx/edx-platform && python manage.py lms openedx_unit_reset_check --settings=tutor.production"
```

## 5. Test route

```bat
curl -i "http://local.openedx.io/api/unit-reset/v1/status/"
```

Nếu route sống nhưng thiếu tham số, kết quả đúng sẽ là HTTP 400 với `MISSING_REQUIRED_FIELDS`.
