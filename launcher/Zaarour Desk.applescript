-- Zaarour Desk launcher. Opens zaarour:// links from the boot loader page.
--   zaarour://morning   fetch today's ads, then open the review screen (same as double-clicking morning.command)
--   zaarour://open/...  open a desk page; starts the desk without fetching if it isn't running
--   zaarour://restart   stop the running desk, then run the morning fetch again
--   zaarour://stop      stop the running desk
-- install.command fills in the two placeholders below.
property deskDir : "__DESK_DIR__"
property python : "__PYTHON__"
property deskURL : "http://127.0.0.1:8765/"

on run
	handle("morning")
end run

on open location theURL
	set AppleScript's text item delimiters to "://"
	set rest to text item 2 of theURL
	set AppleScript's text item delimiters to ""
	handle(rest)
end open location

on handle(rest)
	if rest ends with "/" then set rest to text 1 thru -2 of rest
	if rest is "" or rest is "morning" then
		if isUp() then
			openPage("")
		else
			do shell script "open " & quoted form of (deskDir & "/morning.command")
		end if
	else if rest is "restart" then
		stopDesk()
		do shell script "open " & quoted form of (deskDir & "/morning.command")
	else if rest is "stop" then
		stopDesk()
		display notification "The desk is stopped." with title "Zaarour Desk"
	else if rest starts with "open" then
		set pagePath to ""
		if rest starts with "open/" then set pagePath to text 6 thru -1 of rest
		if not isUp() then
			do shell script "cd " & quoted form of deskDir & " && nohup " & quoted form of python & " -m desk serve --no-browser > /tmp/zaarour-desk.log 2>&1 &"
			delay 1.5
		end if
		openPage(pagePath)
	end if
end handle

on isUp()
	try
		do shell script "curl -sf -o /dev/null -m 1 " & deskURL & "api/counts"
		return true
	on error
		return false
	end try
end isUp

on openPage(pagePath)
	do shell script "open " & quoted form of (deskURL & pagePath)
end openPage

on stopDesk()
	try
		do shell script "lsof -ti tcp:8765 -sTCP:LISTEN | xargs kill"
	end try
	delay 0.5
end stopDesk
