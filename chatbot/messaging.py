import json
import chatbot.api_helper as Api
import chatbot.facebook_helper as Facebook
from chatbot.logger import log
from decimal import Decimal
import re
import random

user_states = {}
greetings = {"hi", "hey", "hello", "greetings"}
stickers = {
    "money": [
        "https://scontent.xx.fbcdn.net/v/t39.1997-6/p100x100/10540972_700042826698549_1238169619_n.png?_nc_ad=z-m&oh=2a5e01b58a747a66478907b91c582743&oe=59561F0B",
        "https://scontent.xx.fbcdn.net/v/t39.1997-6/p100x100/851553_233289396829491_1939290925_n.png?_nc_ad=z-m&oh=4540b6d029e2fde287e34478e250e013&oe=599732C4",
        "https://scontent.xx.fbcdn.net/v/t39.1997-6/p100x100/11409233_850975754950861_1411369750_n.png?_nc_ad=z-m&oh=292b925ffea0d81d2b5e8a7f1b047cec&oe=5986B429",
        "https://scontent.xx.fbcdn.net/v/t39.1997-6/p100x100/10956909_698963033549506_668988407_n.png?_nc_ad=z-m&oh=19d41367fa41cbeaf9ab04f42241f8b8&oe=5995E1DC",
    ]
}


def handle_postback_message(messaging_event):
    message = Facebook.construct_postback_message(messaging_event)
    handle_message(message)


def handle_text_message(messaging_event):
    message = Facebook.construct_message(messaging_event)
    handle_message(message)


def handle_message(message):
    sender_id = message["sender_id"]

    # If we haven't seen the user before, check if the user is registered
    if user_states.get(sender_id) is None:
        success = get_user(sender_id)
        if not success:
            Facebook.send_message(sender_id, "This is embarrassing, I'm having issues contacting the NSA for your "
                                             "private information. Please let the app creators know.")

    user_state = user_states[sender_id]  # This should not be None after get_user

    if user_state["state"] == "idle":
        handle_message_idle(message)

    elif user_state["state"] == "given_task_options":
        handle_message_given_task_options(message)

    elif user_state["state"] == "given_task":
        handle_message_given_task(message)

    # Handle default case
    else:
        Facebook.send_message(sender_id, "You ended up in limbo! I'm sorry, I cannot help you at the moment. "
                              "Contact the app creators with the following information: " + user_state["state"])


def handle_message_idle(message):
    sender_id = message["sender_id"]
    user_state = user_states[sender_id]
    user_id = user_state["user_id"]
    name = user_state["name"]

    # Handle giving task
    if message.get("coordinates") or message.get("quick_reply_payload") == "random_task" or \
       message["text"] == "Random task":
        coordinates = message.get("coordinates", {})
        task = Api.get_random_task(user_id, coordinates.get("long"), coordinates.get("lat"))
        if not task:
            Facebook.send_message(sender_id, "Unfortunately, I could not find a task for you. "
                                             "The most likely reason for this is that there are no available tasks "
                                             "at the moment. Be sure to check back later!")
            return

        questions = task["questions"]
        task_content = task["content"]

        send_task(task_content, questions, sender_id, task)

    # Handle giving multiple tasks
    elif message.get("quick_reply_payload") == "list_task" or message["text"] == "List of tasks":
        tasks = Api.get_tasks(user_id)
        if not tasks:
            Facebook.send_message(sender_id, "Sorry, something went wrong when retrieving your task")
            return

        user_state["state"] = "given_task_options"
        user_state["data"] = {
            "tasks": tasks
        }
        log(user_states)

        elements = []
        mes = "I have " + str(len(tasks)) + " tasks for you:"
        for i, task in enumerate(tasks):
            elements.append({
                "title": "Task " + str(i+1),
                "subtitle": task["description"],
                "buttons": [{
                    "type": "postback",
                    "title": "Do task " + str(i+1),
                    "payload": "task_" + str(i+1)
                }]
            })

        Facebook.send_message(sender_id, mes)
        Facebook.send_list(sender_id, elements)

    # Handle initial message
    else:  # str.lower(message["text"]) in greetings:
        balance = Decimal(-1)
        user_data = Api.get_user_data(user_id)
        if user_data:
            balance = Decimal(user_data["score"])

        quick_replies = [{
            "content_type": "location"
        }, {
            "content_type": "text",
            "title": "Random task",
            "payload": "random_task"
        }, {
            "content_type": "text",
            "title": "List of tasks",
            "payload": "list_task"
        }]

        welcome_back_message = ""
        if balance < 0:
            pass
        elif balance == 0:
            welcome_back_message = "I see you are new here, welcome!"
        else:
            welcome_back_message = ("Welcome back! You have earned €{balance} with us so far, good job!"
                                    .format(balance=balance))

        Facebook.send_message(sender_id, "Hey " + name + "! " + welcome_back_message)
        Facebook.send_message(sender_id, "I can give you a random task or a list of tasks to choose from. "
                                         "If you send your location I can give you a task which can be done near you.",
                              quick_replies)


def handle_message_given_task_options(message):
    message_text = message["text"]
    if message.get("postback"):
        try:
            message_text = message["postback"]
        except:
            pass

    # Find digits in the string
    found_digits = re.findall("\d+", message_text)
    if len(found_digits) < 1:
        Facebook.send_message(message["sender_id"], "I did not understand your choice of task")
        return
    chosen_task_id = int(found_digits[0])

    tasks = user_states[message["sender_id"]]["data"]["tasks"]
    chosen_task = tasks[chosen_task_id - 1]
    questions = chosen_task["questions"]
    task_content = chosen_task["content"]

    log(chosen_task)
    send_task(task_content, questions, message["sender_id"], chosen_task)


def send_task(task_content, questions, sender_id, task):
    user_state = user_states[sender_id]
    user_state["state"] = "given_task"
    user_state["data"] = {
        "task_id": task["taskId"],
        "questions": questions,
        "current_question": 0,
        "content_id": task["contentId"]
    }
    Facebook.send_postback(sender_id,
                           "To skip this task, click the button. You can also skip a task by typing 'Skip'.",
                           "Skip task", "skip_task")

    data_json = json.loads(task_content) if task_content else {}
    question = questions[0]

    question_text = question["question"]

    if data_json.get("pictureUrl") is not None:
        Facebook.send_image(sender_id, data_json["pictureUrl"])

    if data_json.get("question") is not None and data_json.get("answer") is not None:
        Facebook.send_message(sender_id, "Customer tweet:\n" + data_json["question"])
        Facebook.send_message(sender_id, "Webcare answer:\n" + data_json["answer"])

    quick_replies = None
    answer_specification = json.loads(question["answerSpecification"])
    if answer_specification["type"] == "option":
        quick_replies = Facebook.construct_options_quick_replies(answer_specification["options"])

    Facebook.send_message(sender_id, question_text, quick_replies)


def handle_message_given_task(message):
    if message["text"] == "Give me a task":
        Facebook.send_message(message["sender_id"], "You already have a task")
        return

    user_state = user_states[message["sender_id"]]

    if message.get("postback") == "skip_task" or message["text"] == "Skip":
        user_state["state"] = "idle"
        user_state["data"] = None
        Facebook.send_message(message["sender_id"], "Task skipped!")
        handle_message_idle(message)
        return

    current_question = user_state["data"]["current_question"]
    questions = user_state["data"]["questions"]

    answer_specification = json.loads(questions[current_question]["answerSpecification"])
    answer_type = answer_specification["type"]

    answer = None
    if answer_type == "plaintext":
        if not message.get("text"):
            Facebook.send_message(message["sender_id"], "I was expecting text as the answer to this question.")
            return

        answer = message["text"]

    elif answer_type == "image":
        if not message.get("image"):
            Facebook.send_message(message["sender_id"], "I was expecting an image as the answer to this question.")
            return

        answer = message["image"]

    elif answer_type == "option":
        if not message["quick_reply_payload"]:
            # Check if answer matches any of the answer_options
            answer_options = answer_specification["options"]
            if message["text"] not in answer_options:
                quick_replies = Facebook.construct_options_quick_replies(answer_options)
                Facebook.send_message(message["sender_id"],
                                      "I was expecting one of the options as the answer to this question.",
                                      quick_replies)
                return

            answer = message["text"]
        else:
            answer = message["quick_reply_payload"]

    user_id = user_state["user_id"]
    question_id = questions[current_question]["questionId"]
    content_id = user_state["data"]["content_id"]

    res = Api.post_answer(answer, user_id, question_id, content_id, current_question == len(questions) - 1)

    if not res:
        Facebook.send_message(message["sender_id"], "Sorry, something went wrong when submitting your answer")
        return

    if current_question == len(questions) - 1:
        Facebook.send_message(message["sender_id"], "Thank you for your answer, you're done! "
                                                    "You've earned €0,05 completing this task.")
        Facebook.send_image(message["sender_id"], random.choice(stickers["money"]))
        user_state["state"] = "idle"
        user_state["data"] = None

        handle_message_idle(message)

    else:
        user_state["data"]["current_question"] = current_question + 1

        answer_specification = json.loads(questions[current_question + 1]["answerSpecification"])
        answer_type = answer_specification["type"]
        quick_replies = None
        if answer_type == "option":
            quick_replies = Facebook.construct_options_quick_replies(answer_specification["options"])

        Facebook.send_message(message["sender_id"], questions[current_question + 1]["question"], quick_replies)


def get_user(sender_id):
    user = Api.get_user(sender_id)
    if not user:
        return False

    facebook_data = Facebook.get_user(sender_id)
    if not facebook_data:
        return False

    if user_states.get(sender_id) is None:
        user_states[sender_id] = {
            "state": "idle",
            "user_id": user["userId"],
            "name": facebook_data["first_name"],
            "data": None
        }

    return True
