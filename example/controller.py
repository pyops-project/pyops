import json
from lib import infraops

debug = True
group = "Test"
service = "Service"
    
head = """
        <script>
            'use strict';

            function onSend() {
                const type = $("#qtype").val();
                const content = $("#qcontent").val();

                fetch(`/controller/Test/Controller/q?cmd=foo&test1=foo&test2=bar`, {
                    method: type,
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: type == "GET" ? undefined : content
                }).then(async (r)=>{
                    $("#qresult").val(await r.text());
                }).catch((e)=>$("#createModal-error").text(e.toString()));
            }
        </script>
"""

menu = """
                <li class="nav-item m-1">
                    <button type="button" class="btn btn-danger" onclick="onSend()">
                        <span class="material-icons-round">delete_forever</span>
                    </button>
                </li>

                <li class="nav-item mx-auto"></li>

                <li class="nav-item m-1">
                    <button type="button" class="btn btn-danger" onclick="onSend()">
                        <span class="material-icons-round">refresh</span>
                    </button>
                </li>
"""

body = """
        <select  id="qtype" class="form-select">
            <option value="GET">GET</option>
            <option value="DELETE">DELETE</option>
            <option value="PUT">PUT</option>
            <option value="POST">POST</option>
        </select>
        <input id="qcontent" type="text" class="form-control">
        <button type="button" class="btn btn-danger" onclick="onSend()">Send</button>
        <input id="qresult" type="text" class="form-control">
"""