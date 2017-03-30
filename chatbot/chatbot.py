import json
import os
import sys

import falcon
import requests


def add_chatbot_routes(app):
    app.add_route('/chatbot', ChatbotResource())

api_url = "https://fathomless-cove-38602.herokuapp.com"  # no trailing slash
api_methods = {
    'GET': requests.get,
    'POST': requests.post,
    'PUT': requests.put
}

user_states = {}
greetings = {"hi", "hey", "hello", "greetings"}


class ChatbotResource:
    def on_get(self, req, resp):
        # when the endpoint is registered as a webhook, it must echo back
        # the 'hub.challenge' value it receives in the query arguments
        resp.content_type = "text/html"
        if req.get_param("hub.mode") == "subscribe" and req.get_param("hub.challenge"):
            if req.get_param("hub.verify_token") == os.environ["VERIFY_TOKEN"]:
                resp.body = req.get_param("hub.challenge")
            else:
                resp.status = falcon.HTTP_403
                resp.body = "Verification token mismatch"
        else:
            resp.body = "Hello World"

    def on_post(self, req, resp):
        # endpoint for processing incoming messaging events

        data = json.loads(req.stream.read().decode('utf-8'))
        log(data)  # you may not want to log every incoming message in production, but it's good for testing

        if data["object"] == "page":

            for entry in data["entry"]:
                for messaging_event in entry["messaging"]:

                    if messaging_event.get("message"):  # someone sent us a message
                        handle_message(messaging_event)

                    if messaging_event.get("delivery"):  # delivery confirmation
                        pass

                    if messaging_event.get("optin"):  # optin confirmation
                        pass

                    if messaging_event.get("postback"):  # user clicked/tapped "postback" button in earlier message
                        pass

        resp.status = falcon.HTTP_200
        resp.content_type = "text/html"
        resp.body = "ok"


def handle_message(messaging_event):
    message = construct_message(messaging_event)

    # If we haven't seen the user before, check if the user is registered
    if user_states.get(message["sender_id"]) is None:
        get_user(message["sender_id"])

    user_state = user_states[message["sender_id"]]  # This should not be None after get_user

    if user_state["state"] == "idle":
        handle_message_idle(message)

    elif user_state["state"] == "given_task":
        handle_message_given_task(message)

    # Handle default case
    else:
        send_message(message["sender_id"], "I did not understand your message")


def handle_message_idle(message):
    # Handle giving task
    if message.get("coordinates") or message.get("quick_reply_payload") == "task" or message["text"] == "Give me a task":
        task = get_random_task()
        if not task:
            send_message(message["sender_id"], "Sorry, something went wrong when retrieving your task")
            return

        questions = task["questions"]
        data_json = json.loads(task["content"])[0]  # TODO: When do we have multiple content?

        user_states[message["sender_id"]] = {
            "state": "given_task",
            "user_id": user_states[message["sender_id"]]["user_id"],
            "task_id": task["taskId"],
            "questions": questions,
            "current_question": 0,
            "content_id": task["contentId"]
        }
        log(user_states)

        send_image(message["sender_id"], data_json["pictureUrl"])
        send_message(message["sender_id"], questions[0]["question"])

    # Handle initial message
    else:  # str.lower(message["text"]) in greetings:
        quick_replies = [{
            "content_type": "location"
        }, {
            "content_type": "text",
            "title": "Give me a task",
            "payload": "task"
        }]

        send_message(message["sender_id"], "What's up? I can give you a task, but if you send your location "
                                           "I can give you even cooler tasks.", quick_replies)


def handle_message_given_task(message):
    if message["text"] == "Give me a task":
        send_message(message["sender_id"], "You already have a task")
        return

    user_state = user_states[message["sender_id"]]
    current_question = user_state["current_question"]
    questions = user_state["questions"]
    answer_type = questions[current_question]["answerType"]

    answer = None
    if answer_type == "plaintext":
        if not message["text"]:
            send_message(message["sender_id"], "I was expecting text as an answer to this question..")
            return

        answer = message["text"]

    if answer_type == "image":
        if not message["image"]:
            send_message(message["sender_id"], "I was expecting an image as an answer to this question..")
            return

        answer = message["image"]

    user_id = user_state["user_id"]
    question_id = questions[current_question]["questionId"]
    content_id = user_state["content_id"]

    res = post_answer(answer, user_id, question_id, content_id)

    if not res:
        send_message(message["sender_id"], "Sorry, something went wrong when submitting your answer")
        return

    if current_question == len(questions) - 1:
        send_message(message["sender_id"], "Thank you for your answer, you're done!")
        user_states[message["sender_id"]] = {
            "state": "idle",
            "user_id": user_state["user_id"]
        }

        handle_message_idle(message)

    else:
        user_state["current_question"] = current_question + 1
        send_message(message["sender_id"], "Thank you for your answer, here comes the next question!")
        send_message(message["sender_id"], questions[current_question + 1]["question"])


def construct_message(messaging_event):
    message = {}

    message["sender_id"] = messaging_event["sender"]["id"]  # the facebook ID of the person sending you the message

    message["text"] = messaging_event["message"].get("text", "")

    quick_reply = messaging_event["message"].get("quick_reply")
    message["quick_reply_payload"] = quick_reply["payload"] if quick_reply else None

    attachments = messaging_event["message"].get("attachments")
    if attachments is not None:
        attachment = attachments[0]  # Pick first attachment, discard the rest
        attachment_type = attachment["type"]
        if attachment_type == "location":
            message["coordinates"] = attachment["payload"]["coordinates"]
        if attachment_type == "image":
            message["image"] = attachment["payload"]["url"]

    return message


def get_user(sender_id):
    #TODO: Get user information from database and store it in states
    #TODO: Register user if not registered yet
    user = call_api("GET", "/worker/users");
    if not user:
        return False

    if user_states.get(sender_id) is None:
        user_states[sender_id] = {
            "state": "idle",
            "user_id": user["userId"]
        }

## Facebook functions
def facebook_send(data):
    params = {
        "access_token": os.environ["PAGE_ACCESS_TOKEN"]
    }
    headers = {
        "Content-Type": "application/json"
    }

    log("Sending to Facebook: {data}".format(data=data))
    r = requests.post("https://graph.facebook.com/v2.8/me/messages", params=params, headers=headers, data=data)
    if r.status_code != 200:
        log("{status_code} encountered when sending Facebook data".format(status_code=r.status_code))
        log(r.text)
        return False

    return True

def send_message(recipient_id, message_text, quick_replies=None):
    log("sending message to {recipient}: {text}".format(recipient=recipient_id, text=message_text))

    data = json.dumps({
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "text": message_text,
            "quick_replies": quick_replies
        }
    })

    res = facebook_send(data)
    return res


def send_image(recipient_id, image_url):
    log("sending image to {recipient}: {image}".format(recipient=recipient_id, image=image_url))

    data = json.dumps({
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "attachment": {
              "type": "image",
              "payload": {
                "url": image_url
              }
            }
        }
    })

    res = facebook_send(data)
    return res


## API functions
def call_api(method, url, data=None):
    r_method = api_methods.get(method.upper())
    if r_method is None:
        log("Unknown method {method} for API call".format(method=method))
        return False

    call_url = api_url + url
    r = r_method(call_url, json=data)

    if r.status_code != 200:
        log("{status_code} encountered when calling {url}".format(status_code=r.status_code, url=call_url))
        log(r.text)
        return False

    return r.json()

def get_random_task():
    res = call_api("GET", "/worker/tasks?order=random&limit=1")

    if not res:
        return False

    task = res[0]  # Pick the only question in the list
    return task

def post_answer(answer, user_id, question_id, content_id):
    data = {
        "answer": answer,
        "userId": user_id,
        "questionId": question_id,
        "contentId": content_id  # TODO: Why is contentId needed?
    }

    res = call_api("POST", "/worker/answers", data)
    if not res:
        return False

    return True


## Heroku functions
def log(message):  # simple wrapper for logging to stdout on heroku
    print(str(message))
    sys.stdout.flush()
