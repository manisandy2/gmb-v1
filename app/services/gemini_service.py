
def analyze_review(review_data):

    return call_gemini_sync(
        review_data["comment"],
        review_data["stars"],
        review_data["reviewer"],
        review_data["store"]
    )