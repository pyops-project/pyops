while True:
    msg = listener.listen()
    
    print(msg)

    if msg == "EXIT":
        listener.response("BYE")
        break
    else:
        listener.response(msg)