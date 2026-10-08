"""Generates ready-to-install scheduler files with your real paths filled in.

    jobradar schedule macos   --time 08:15
    jobradar schedule windows --time 08:15,17:15     (several times a day)
    jobradar schedule linux   --time 08:15
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape


def _paths(cfg):
    py = Path(sys.executable).resolve()
    claude = shutil.which(cfg.get("llm.claude_bin") or "claude")
    claude_dir = str(Path(claude).parent) if claude else ""
    return py, cfg.home, claude_dir


def macos(cfg, times: list[tuple[int, int]]) -> str:
    py, home, claude_dir = _paths(cfg)
    path_env = ":".join(x for x in [claude_dir, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] if x)
    log = home / "data" / "logs" / "scheduler.log"
    intervals = "".join(f"\n    <dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>"
                        for h, m in times)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jobradar.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>{escape(str(py))}</string><string>-m</string><string>jobradar</string>
    <string>--home</string><string>{escape(str(home))}</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>{escape(str(home))}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{escape(path_env)}</string></dict>
  <key>StartCalendarInterval</key>
  <array>{intervals}
  </array>
  <key>StandardOutPath</key><string>{escape(str(log))}</string>
  <key>StandardErrorPath</key><string>{escape(str(log))}</string>
</dict>
</plist>
"""
    out = home / "scheduling" / "com.jobradar.daily.plist"
    out.parent.mkdir(exist_ok=True)
    out.write_text(plist, encoding="utf-8")
    return (f"נוצר {out}\nהתקנה (פעם אחת, בטרמינל):\n"
            f"  cp '{out}' ~/Library/LaunchAgents/\n"
            f"  launchctl load ~/Library/LaunchAgents/com.jobradar.daily.plist\n"
            f"בדיקה מיידית:  launchctl start com.jobradar.daily   (ואז לבדוק {log})\n"
            "אם המחשב ישן בשעה הזו – ההרצה תתבצע כשיתעורר. אם הוא כבוי – תדלג לפעם הבאה.")


def windows(cfg, times: list[tuple[int, int]]) -> str:
    py, home, _ = _paths(cfg)
    triggers = ", ".join(f"(New-ScheduledTaskTrigger -Daily -At {h:02d}:{m:02d})" for h, m in times)
    when = ", ".join(f"{h:02d}:{m:02d}" for h, m in times)
    ps = f"""# JobRadar daily task - run this file once in PowerShell
$action   = New-ScheduledTaskAction -Execute "{py}" -Argument '-m jobradar --home "{home}" run' -WorkingDirectory "{home}"
$trigger  = @({triggers})
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "JobRadar" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "JobRadar scheduled daily at {when}. Test now with: Start-ScheduledTask -TaskName JobRadar"
"""
    out = home / "scheduling" / "register_task.ps1"
    out.parent.mkdir(exist_ok=True)
    out.write_text(ps, encoding="utf-8")
    return (f"נוצר {out}\nהתקנה: פתח PowerShell (לא כמנהל) והרץ:\n"
            f"  powershell -ExecutionPolicy Bypass -File \"{out}\"\n"
            "StartWhenAvailable = אם המחשב היה כבוי/ישן בשעה הזו, המשימה תרוץ כשיעלה.")


def windows_inbox(cfg, times: list[tuple[int, int]]) -> str:
    """Daily push + tracker window (pythonw: no console), and a Desktop shortcut to open it any time."""
    py, home, _ = _paths(cfg)
    pyw = py.with_name("pythonw.exe")
    exe = pyw if pyw.exists() else py
    triggers = ", ".join(f"(New-ScheduledTaskTrigger -Daily -At {h:02d}:{m:02d})" for h, m in times)
    when = ", ".join(f"{h:02d}:{m:02d}" for h, m in times)
    ps = f"""# JobRadar tracker - daily push + window, and a Desktop shortcut. Run this file once in PowerShell.
$action   = New-ScheduledTaskAction -Execute "{exe}" -Argument '-m jobradar --home "{home}" inbox --push' -WorkingDirectory "{home}"
$trigger  = @({triggers})
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Register-ScheduledTask -TaskName "JobRadar Inbox" -Action $action -Trigger $trigger -Settings $settings -Force

$desktop  = [Environment]::GetFolderPath("Desktop")
$shell    = New-Object -ComObject WScript.Shell
$lnk      = $shell.CreateShortcut((Join-Path $desktop "JobRadar.lnk"))
$lnk.TargetPath       = "{exe}"
$lnk.Arguments        = '-m jobradar --home "{home}" inbox'
$lnk.WorkingDirectory = "{home}"
$lnk.Description      = "JobRadar - job tracker"
$lnk.Save()
Write-Host "JobRadar Inbox scheduled daily at {when}, shortcut JobRadar on the Desktop. Test now with: Start-ScheduledTask -TaskName 'JobRadar Inbox'"
"""
    out = home / "scheduling" / "register_inbox_task.ps1"
    out.parent.mkdir(exist_ok=True)
    out.write_text(ps, encoding="utf-8-sig")  # BOM: Windows PowerShell 5.1 reads it as UTF-8
    return (f"נוצר {out}\nהתקנה: פתח PowerShell (לא כמנהל) והרץ:\n"
            f"  powershell -ExecutionPolicy Bypass -File \"{out}\"\n"
            f"בכל יום ב-{when}: פוש לטלפון עם הסיכום, וחלון המעקב נפתח במחשב.\n"
            "בכל רגע אחר: קיצור הדרך JobRadar בשולחן העבודה.")


def linux(cfg, times: list[tuple[int, int]]) -> str:
    py, home, claude_dir = _paths(cfg)
    prefix = f"PATH={claude_dir}:$PATH " if claude_dir else ""
    line = "\n".join(f"{m} {h} * * * cd '{home}' && {prefix}'{py}' -m jobradar run "
                     f">> '{home}/data/logs/scheduler.log' 2>&1" for h, m in times)
    out = home / "scheduling" / "crontab.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(line + "\n", encoding="utf-8")
    return (f"נוצר {out}\nהתקנה: crontab -e  והדבק את השורות:\n  {line}\n"
            "שים לב: cron לא משלים הרצות שהוחמצו כשהמחשב כבוי (systemd timer עם Persistent=true כן).")


GENERATORS = {"macos": macos, "windows": windows, "linux": linux}
