import json

while not listener.kill_now:
    msg = listener.listen()

    if msg != "TERM":            
        try:
            print(msg)

            if "cmd" in msg:
                match msg["cmd"].lower():
                    case "exit":
                        listener.response("BYE")
                    case _:
                        listener.response(f"unknown message\n{json.dumps(msg, sort_keys=True, indent=4)}")
            else:
                listener.response(f"malformed message\n{json.dumps(msg, sort_keys=True, indent=4)}")
        except Exception as ex:
            listener.response(f"broken message\n{json.dumps(msg, sort_keys=True, indent=4)}\n{str(traceback.format_exc())}")
