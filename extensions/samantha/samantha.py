from flask import request, render_template_string, Response, stream_with_context
from openai import OpenAI
import config

# Initialize the OpenAI client with no restrictive timeout limits
client = OpenAI(base_url="http://100.118.11.83:11434/v1", api_key="x")

DOMAIN = "ai.nodemixaholic.com"

messages = []
selected_model = "sparksammy/samantha-combo-3-small:latest"
previous_model = selected_model

system_prompts = [
	{"role": "system", "content": "Please provide your response in plain text using only ASCII characters. "
		"Never use any special or esoteric characters that might not be supported by older systems. "},
	{"role": "system", "content": "Your responses will be presented to the user within "
		"the body of an html document. Be aware that any html tags you respond with will be interpreted and rendered as html. "
		"Therefore, when discussing an html tag, do not wrap it in <>, as it will be rendered as html. Instead, wrap the name "
		"of the tag in <b> tags to emphasize it, for example \"the <b>a</b> tag\". "
		"You do not need to provide a <body> tag. "
		"When responding with a list, ALWAYS format it using <ol> or <ul> with individual list items wrapped in <li> tags. "
		"When responding with a link,  the <a> tag."},
	{"role": "system", "content": "When responding with code or other formatted text (including prose or poetry), always insert "
		"<pre></pre> tags with <code></code> tags nested inside (which contain the formatted content)."
		"If the user asks you to respond 'in a code block', this is what they mean. NEVER use three backticks "
		"(```like so``` (markdown style)) when discussing code. If you need to highlight a variable name or text of similar (short) length, "
		"wrap it in <code> tags (without the aforementioned <pre> tags). Do not forget to close html tags where appropriate. "
		"When using a code block, ensure that individual lines of text do not exceed 60 characters."},
	{"role": "system", "content": "NEVER use **this format** (markdown style) to bold text  - instead, wrap text in <b> tags or <i> "
		"tags (when appropriate) to emphasize it."},
]

# Split HTML layout so we can stream text dynamically into the middle of the document
HTML_TOP = """<!DOCTYPE html>
<html lang="en">
<head>
	<meta charset="UTF-8">
	<title>Samantha</title>
</head>
<body>
	<form method="post" action="/">
		<select id="model" name="model">
			<option value="sparksammy/samantha-combo-3-small:latest" {% if selected_model == 'sparksammy/samantha-combo-3-small:latest' %}selected{% endif %}>Combo 3 Small</option>
		</select>
		<input type="text" size="63" name="command" required autocomplete="off">
		<input type="submit" value="Submit">
	</form>
	<div id="chat">
"""

HTML_BOTTOM = """	</div>
</body>
</html>
"""

def handle_request(req):
	if req.method == 'POST':
		# Return a streaming Flask response object for POST requests
		return Response(stream_with_context(generate_stream(req)), mimetype='text/html')
	elif req.method == 'GET':
		content, status_code = handle_get(req)
		return content, status_code
	else:
		return "Not Found", 404

def handle_get(request):
	# Build initial empty view state on GET
	output = ""
	for msg in reversed(messages[-10:]):
		if msg['role'] == 'user':
			output += f"<b>User:</b> {msg['content']}<br>"
		elif msg['role'] == 'system':
			output += f"<b>Samantha:</b> {msg['content']}<br>"
	
	full_page = render_template_string(HTML_TOP, selected_model=selected_model) + output + HTML_BOTTOM
	return full_page, 200

def generate_stream(request):
	global messages, selected_model, previous_model
	
	user_input = request.form['command']
	selected_model = request.form['model']

	if selected_model != previous_model:
		previous_model = selected_model
		messages = [{"role": "user", "content": user_input}]
	else:
		messages.append({"role": "user", "content": user_input})

	messages_to_send = system_prompts + messages[-10:]

	# 1. Immediately send the top half of the HTML layout to IE5 to reset its timeout clock
	yield render_template_string(HTML_TOP, selected_model=selected_model)

	# 2. Render previous chat histories first
	history_output = ""
	for msg in reversed(messages[:-1][-10:]):  # Exclude current user message for historical order rendering
		if msg['role'] == 'user':
			history_output += f"<b>User:</b> {msg['content']}<br>"
		elif msg['role'] == 'system':
			history_output += f"<b>Samantha:</b> {msg['content']}<br>"
	yield history_output

	# Render current user prompt
	yield f"<b>User:</b> {user_input}<br><b>Samantha:</b> "

	# 3. Request a streaming response from the local LLM
	response = client.chat.completions.create(
		model=selected_model,
		messages=messages_to_send,
		stream=True
	)

	full_response_text = ""
	# 4. As tokens arrive, yield them raw to the browser chunk by chunk
	for chunk in response:
		if chunk.choices and chunk.choices[0].delta.content:
			token = chunk.choices[0].delta.content
			full_response_text += token
			yield token

	# Finalize chat state memory
	messages.append({"role": "system", "content": full_response_text})
	
	# 5. Send closing tags to complete the DOM tree cleanly
	yield "<br>" + HTML_BOTTOM