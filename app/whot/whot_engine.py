"""
Whot Card Game Engine.
Implements the deck generator, move validation, action handling, and AI heuristics.
"""
import random

SUITS = ["circle", "triangle", "cross", "star", "square"]

# Circular suit: 1-5, 7, 8, 10-14
CIRCLE_VALUES = [1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 14]
# Triangle suit: 1-5, 7, 8, 10-14
TRIANGLE_VALUES = [1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 14]
# Cross suit: 1-3, 5, 7, 8, 10, 11, 13, 14
CROSS_VALUES = [1, 2, 3, 5, 7, 8, 10, 11, 13, 14]
# Star suit: 1-5, 7, 8, 10-14
STAR_VALUES = [1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 14]
# Square suit: 1-3, 5, 7, 9, 10-14
SQUARE_VALUES = [1, 2, 3, 5, 7, 9, 10, 11, 12, 13, 14]

def create_deck():
    """Generates and shuffles a standard 54-card Whot deck."""
    deck = []
    
    # Add regular suits
    for v in CIRCLE_VALUES:
        deck.append({"suit": "circle", "value": v})
    for v in TRIANGLE_VALUES:
        deck.append({"suit": "triangle", "value": v})
    for v in CROSS_VALUES:
        deck.append({"suit": "cross", "value": v})
    for v in STAR_VALUES:
        deck.append({"suit": "star", "value": v})
    for v in SQUARE_VALUES:
        deck.append({"suit": "square", "value": v})
        
    # Add 5 Whot (20) Wildcards
    for _ in range(5):
        deck.append({"suit": "whot", "value": 20})
        
    random.shuffle(deck)
    return deck


def is_card_playable(card, top_card, called_suit=None, active_penalty_picks=0, penalty_type=None):
    """
    Validates if a card can be played on top of the current discard pile.
    Handles penalty pick stacking rules (Pick 2 or Pick 3).
    """
    # 1. Stacking/Defending Penalty check
    if active_penalty_picks > 0:
        if penalty_type == 2:
            # Must play another Pick 2 (value 2) or a Whot (20) to defend/redirect
            return card["value"] == 2 or card["value"] == 20
        elif penalty_type == 3:
            # Must play another Pick 3 (value 5) or a Whot (20) to defend/redirect
            return card["value"] == 5 or card["value"] == 20
        return False

    # 2. Standard matching rules
    # Whot wildcard is always playable
    if card["value"] == 20 or card["suit"] == "whot":
        return True

    # If top card is Whot (20) and a shape was called, must match shape
    if top_card["value"] == 20:
        if called_suit:
            return card["suit"] == called_suit
        return True

    # Otherwise, match suit/shape or value
    return card["suit"] == top_card["suit"] or card["value"] == top_card["value"]


def get_bot_play(bot_hand, top_card, called_suit=None, active_penalty_picks=0, penalty_type=None):
    """
    Greedy AI opponent decision logic.
    Returns: (card_to_play, called_shape) or (None, None) if bot must draw.
    """
    # 1. Defending Pick 2 / Pick 3 stacking
    if active_penalty_picks > 0:
        for idx, card in enumerate(bot_hand):
            if is_card_playable(card, top_card, called_suit, active_penalty_picks, penalty_type):
                # Bot will play it to stack/defend
                return idx, get_bot_called_shape(bot_hand, idx)
        return None, None

    # 2. General play logic
    playable_indices = []
    for idx, card in enumerate(bot_hand):
        if is_card_playable(card, top_card, called_suit, 0):
            playable_indices.append(idx)

    if not playable_indices:
        return None, None

    # Bot prefers playing Action cards first to disrupt player
    action_indices = [i for i in playable_indices if bot_hand[i]["value"] in (1, 2, 5, 8, 14, 20)]
    if action_indices:
        # Choose a random action card
        play_idx = random.choice(action_indices)
    else:
        # Choose card of the suit bot has the most of in hand
        suit_counts = {}
        for c in bot_hand:
            if c["suit"] != "whot":
                suit_counts[c["suit"]] = suit_counts.get(c["suit"], 0) + 1
        
        # Sort playable cards by count of their suit in hand
        playable_indices.sort(key=lambda i: suit_counts.get(bot_hand[i]["suit"], 0), reverse=True)
        play_idx = playable_indices[0]

    return play_idx, get_bot_called_shape(bot_hand, play_idx)


def get_bot_called_shape(bot_hand, played_index):
    """Determines what shape the bot should call if it plays a Whot (20) wildcard."""
    played_card = bot_hand[played_index]
    if played_card["value"] != 20:
        return None

    # Count shapes in bot's hand (excluding the played Whot card)
    suit_counts = {s: 0 for s in SUITS}
    for idx, c in enumerate(bot_hand):
        if idx != played_index and c["suit"] in suit_counts:
            suit_counts[c["suit"]] += 1

    # Select the shape that bot holds the most of
    called = max(suit_counts, key=suit_counts.get)
    return called
