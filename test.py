if linter_name == "pylint":
  result = subprocess.run(
    ["pylint", "--from-stdin", "--output-format=json"],
    input=code,
    text=True,
    capture_output=True,
    check=True
  )
  return json.loads(result.stdout)
