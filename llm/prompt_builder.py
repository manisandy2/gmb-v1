# from llm.config import EMAIL
from llm.reply_selector import get_reply_style


def build_prompt(customer, rating, store, review_text):

    style = get_reply_style(rating)

    sentiment = style["sentiment"]
    selected_tone = style["tone"]
    example_opening = style["opening"]

    prompt = f"""
You are a sentiment, emotion, and attribute analyzer for Poorvika.

TASK:
1. Analyze the customer review
2. Generate a personalized reply.

REVIEW DETAILS:
Customer Name: {customer or "<Name>"}
Star Rating: {rating}/5
Store Location: {store or "<Location>"}
Review Text: "{review_text}"

ANALYSIS REQUIREMENTS:

1. Sentiment:
positive | neutral | negative

2. Emotion:
joy | sadness | anger | fear | surprise | disgust | other

3. Attributes:
product | service | delivery | pricing | staff | store experience | other

REPLY RULES:

Tone style:
{selected_tone}

Opening example:
{example_opening}

Guidelines:
• Single paragraph
• 4–5 sentences
• 60–80 words
• Mention store location naturally
• No "Dear"
• No closing like "Regards"

Negative reviews must include:
Please contact us at {EMAIL}

Return ONLY JSON.

{{
"sentiment": "<positive|neutral|negative>",
"emotion": "<joy|sadness|anger|fear|surprise|disgust|other>",
"attributes": ["<category1>", "<category2>"],
"star_rating": {rating},
"reply": "<single paragraph reply>"
}}
"""

    return prompt