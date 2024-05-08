while self.listener.kill_now:
    msg = listener.listen()
    
    print(msg)

    if msg == "TERM":
        pass
    if msg == "EXIT":
        listener.response("BYE")
        break
    else:
        listener.response(msg)