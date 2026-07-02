#!/usr/bin/env bash
set -euo pipefail

SWITCH_URL="${SWITCH_URL:-http://192.168.0.220/}"
SWITCH_PASSWORD="${SWITCH_PASSWORD:-123456}"
OFF_OPCODE="${OFF_OPCODE:-50}"
ON_OPCODE="${ON_OPCODE:-562}"
OFF_SECONDS="${OFF_SECONDS:-5}"

chrome_js() {
  /usr/bin/osascript - "$1" <<'APPLESCRIPT'
on run argv
  tell application "Google Chrome"
    if not (exists window 1) then make new window
    execute active tab of front window javascript (item 1 of argv)
  end tell
end run
APPLESCRIPT
}

js_string() {
  /usr/bin/python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

post_json() {
  local cmd="$1"
  local calldata="${2:-null}"

  chrome_js "(function(){
    var payload={data:{callcmd:${cmd}}};
    var calldata=${calldata};
    if(calldata!==null){payload.data.calldata=calldata;}
    var xhr=new XMLHttpRequest();
    xhr.open('POST','/${cmd}',false);
    xhr.setRequestHeader('Content-Type','application/json; charset=utf-8');
    xhr.send(JSON.stringify(payload));
    return xhr.responseText;
  })()"
}

echo "Opening switch page: ${SWITCH_URL}"
/usr/bin/osascript <<APPLESCRIPT
tell application "Google Chrome"
  if not (exists window 1) then make new window
  set URL of active tab of front window to "${SWITCH_URL}"
end tell
APPLESCRIPT

sleep 2

password_json="$(printf '%s' "${SWITCH_PASSWORD}" | js_string)"

echo "Login..."
post_json 123 "{password:${password_json}}"
echo

echo "PoE OFF on physical port 1 (opcode ${OFF_OPCODE})..."
post_json 103 "{opcode:${OFF_OPCODE}}"
echo

echo "Waiting ${OFF_SECONDS}s..."
sleep "${OFF_SECONDS}"

echo "PoE ON on physical port 1 (opcode ${ON_OPCODE})..."
post_json 103 "{opcode:${ON_OPCODE}}"
echo

echo "Final status..."
post_json 101
echo
