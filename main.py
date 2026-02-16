from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
import itertools
from collections import Counter
from enum import Enum

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 1. データモデルと役判定ロジック ---
class Phase(str, Enum):
    PREFLOP = "PREFLOP"
    FLOP = "FLOP"
    TURN = "TURN"
    RIVER = "RIVER"
    SHOWDOWN = "SHOWDOWN"

class Card:
    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank
    def to_dict(self):
        suits_symbol = {"Spades": "♠", "Hearts": "♥", "Diamonds": "♦", "Clubs": "♣"}
        ranks_str = {11: "J", 12: "Q", 13: "K", 14: "A"}
        r = ranks_str.get(self.rank, str(self.rank))
        return {"display": f"{suits_symbol[self.suit]}{r}"}

class Deck:
    def __init__(self):
        self.cards = [Card(s, r) for s in ["Spades", "Hearts", "Diamonds", "Clubs"] for r in range(2, 15)]
        random.shuffle(self.cards)
    def draw(self):
        return self.cards.pop()

class Player:
    def __init__(self, player_id, name, stack):
        self.id = player_id
        self.name = name
        self.stack = stack
        self.hand = []
        self.current_bet = 0
        self.is_active = True
    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "stack": self.stack,
            "current_bet": self.current_bet, "is_active": self.is_active,
            "hand": [c.to_dict() for c in self.hand]
        }

# --- 役判定関数（以前作成したもの） ---
def evaluate_hand_strict(cards):
    ranks = [c.rank for c in cards]
    suits = [c.suit for c in cards]
    rank_counts = Counter(ranks)
    sorted_ranks = sorted(ranks, key=lambda x: (rank_counts[x], x), reverse=True)
    counts = sorted(rank_counts.values(), reverse=True)
    is_flush = len(set(suits)) == 1
    is_straight = (len(set(ranks)) == 5) and (max(ranks) - min(ranks) == 4)
    if set(ranks) == {14, 5, 4, 3, 2}:
        is_straight = True
        sorted_ranks = [5, 4, 3, 2, 14]
        
    if is_straight and is_flush: hand_score = 8
    elif counts == [4, 1]:       hand_score = 7
    elif counts == [3, 2]:       hand_score = 6
    elif is_flush:               hand_score = 5
    elif is_straight:            hand_score = 4
    elif counts == [3, 1, 1]:    hand_score = 3
    elif counts == [2, 2, 1]:    hand_score = 2
    elif counts == [2, 1, 1, 1]: hand_score = 1
    else:                        hand_score = 0
    return (hand_score, sorted_ranks)

def get_best_hand(seven_cards):
    best_eval = (-1, [])
    for combo in itertools.combinations(seven_cards, 5):
        current_eval = evaluate_hand_strict(combo)
        if current_eval > best_eval:
            best_eval = current_eval
    return best_eval

# --- 2. API用のゲーム管理エンジン ---
class TexasHoldemEngine:
    def __init__(self):
        self.players = {"p1": Player("p1", "あなた", 1000), "p2": Player("p2", "CPU", 1000)}
        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.highest_bet = 0
        self.phase = Phase.PREFLOP
        self.current_turn = "p1"
        self.message = "ゲームを開始してください"
        self.actions_this_round = 0

    def start_new_hand(self):
        # どちらかのチップが0以下の場合はゲームを開始しない
        if self.players["p1"].stack <= 0 or self.players["p2"].stack <= 0:
            self.message = "チップがありません。リセットしてください。"
            return

        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.phase = Phase.PREFLOP
        self.actions_this_round = 0
        for p in self.players.values():
            p.hand = [self.deck.draw(), self.deck.draw()]
            p.current_bet = 0
            p.is_active = True
        
        # ★修正: 所持金以上のブラインドを引かない（強制オールイン対応）
        p1_blind = min(10, self.players["p1"].stack)
        self.players["p1"].stack -= p1_blind
        self.players["p1"].current_bet = p1_blind
        
        p2_blind = min(20, self.players["p2"].stack)
        self.players["p2"].stack -= p2_blind
        self.players["p2"].current_bet = p2_blind
        
        self.highest_bet = max(p1_blind, p2_blind)
        self.pot = p1_blind + p2_blind
        
        self.current_turn = "p1"
        self.message = "ゲーム開始！あなたの番です。"

    def reset_game(self):
        """チップを初期状態に戻してゲームをリセットする"""
        self.players["p1"].stack = 1000
        self.players["p2"].stack = 1000
        self.start_new_hand()
        self.message = "【リセット】チップが初期化されました。あなたの番です！"

    def process_action(self, player_id: str, action_type: str, amount: int):
        self._apply_action(player_id, action_type, amount)
        if self.phase == Phase.SHOWDOWN:
            return

        self._check_round_end() # 新しく作ったラウンド終了判定を呼び出す

        # ラウンドが続いていて、CPUの番なら自動進行
        if self.phase != Phase.SHOWDOWN and self.current_turn == "p2":
            self._play_ai_turn()

    def _apply_action(self, player_id: str, action_type: str, amount: int):
        player = self.players[player_id]
        self.actions_this_round += 1

        if action_type == "fold":
            player.is_active = False
            self.phase = Phase.SHOWDOWN
            winner = self.players["p2" if player_id == "p1" else "p1"]
            winner.stack += self.pot
            self.message = f"【決着】{player.name} がフォールドしました。 {winner.name} がポット {self.pot} を獲得！"
            self.pot = 0
            
        elif action_type == "call":
            call_amount = self.highest_bet - player.current_bet
            # ★修正箇所：所持金以上のコールはできない（オールイン扱い）
            if call_amount >= player.stack:
                call_amount = player.stack
                self.message = f"🔥 {player.name} がオールイン（全額コール）しました！"
            else:
                self.message = f"{player.name} がコール/チェックしました。"
                
            player.stack -= call_amount
            player.current_bet += call_amount
            self.pot += call_amount
            self.current_turn = "p2" if player_id == "p1" else "p1"
            
        elif action_type == "raise":
            call_amount = self.highest_bet - player.current_bet
            total_bet = call_amount + amount
            
            # ★修正箇所：所持金以上のレイズはできない（オールイン扱い）
            if total_bet >= player.stack:
                total_bet = player.stack
                amount = total_bet - call_amount # 追加レイズ額を再計算
                self.message = f"🔥 {player.name} がオールイン（全額ベット）しました！"
            else:
                self.message = f"{player.name} が {amount} チップを追加レイズしました！"
                
            player.stack -= total_bet
            player.current_bet += total_bet
            if player.current_bet > self.highest_bet:
                self.highest_bet = player.current_bet
            self.pot += total_bet
            self.current_turn = "p2" if player_id == "p1" else "p1"

    def _play_ai_turn(self):
        import random
        choice = random.random()
        
        # CPUのコールに必要な額を計算し、足りない場合はレイズさせない
        call_amount = self.highest_bet - self.players["p2"].current_bet
        if self.players["p2"].stack <= call_amount:
            self._apply_action("p2", "call", 0)
        else:
            if choice < 0.2: self._apply_action("p2", "raise", 50)
            else:            self._apply_action("p2", "call", 0)
            
        self._check_round_end()

    # --- ★新規追加: ベットラウンドの終了判定と返金処理 ---
    def _check_round_end(self):
        p1, p2 = self.players["p1"], self.players["p2"]
        is_round_over = False
        
        # 1. ベット額が一致し、両者が行動済み
        if p1.current_bet == p2.current_bet and self.actions_this_round >= 2:
            is_round_over = True
        # 2. 額が一致していなくても、少ない方がオールインしていれば終了
        elif p1.current_bet > p2.current_bet and p2.stack == 0:
            is_round_over = True
        elif p2.current_bet > p1.current_bet and p1.stack == 0:
            is_round_over = True
            
        if is_round_over:
            # 相手がオールインして額が揃わなかった場合、多く賭けすぎた分を返金する
            if p1.current_bet > p2.current_bet:
                diff = p1.current_bet - p2.current_bet
                p1.stack += diff
                self.pot -= diff
                p1.current_bet = p2.current_bet
            elif p2.current_bet > p1.current_bet:
                diff = p2.current_bet - p1.current_bet
                p2.stack += diff
                self.pot -= diff
                p2.current_bet = p1.current_bet
                
            self.advance_phase()
            if self.phase != Phase.SHOWDOWN:
                self.current_turn = "p1"
                self.message += " 次のカードが開かれました。あなたの番です。"

    def advance_phase(self):
        self.actions_this_round = 0
        
        # ★追加: どちらかがオールインしているか判定
        is_all_in = self.players["p1"].stack == 0 or self.players["p2"].stack == 0

        if self.phase == Phase.PREFLOP:
            self.phase = Phase.FLOP
            self.community_cards.extend([self.deck.draw() for _ in range(3)])
        elif self.phase == Phase.FLOP:
            self.phase = Phase.TURN
            self.community_cards.append(self.deck.draw())
        elif self.phase == Phase.TURN:
            self.phase = Phase.RIVER
            self.community_cards.append(self.deck.draw())
        elif self.phase == Phase.RIVER:
            self.phase = Phase.SHOWDOWN
            self.evaluate_winner()
            return
        
        self.highest_bet = 0
        for p in self.players.values():
            p.current_bet = 0

        # ★追加: オールイン中なら、途中で止めずに次のフェーズも一気に進める
        if is_all_in and self.phase != Phase.SHOWDOWN:
            self.advance_phase()

    def evaluate_winner(self):
        p1, p2 = self.players["p1"], self.players["p2"]
        p1_seven = p1.hand + self.community_cards
        p2_seven = p2.hand + self.community_cards
        
        p1_eval = get_best_hand(p1_seven)
        p2_eval = get_best_hand(p2_seven)
        
        hand_names = ["ハイカード", "ワンペア", "ツーペア", "スリーカード", "ストレート", "フラッシュ", "フルハウス", "フォーカード", "ストレートフラッシュ"]
        name1 = hand_names[p1_eval[0]]
        name2 = hand_names[p2_eval[0]]

        if p1_eval > p2_eval:
            p1.stack += self.pot
            self.message = f"【決着】あなたの勝利！（{name1} vs {name2}） ポット {self.pot} 獲得！"
        elif p2_eval > p1_eval:
            p2.stack += self.pot
            self.message = f"【決着】CPUの勝利！（{name2} vs {name1}） ポット {self.pot} を奪われました。"
        else:
            p1.stack += self.pot // 2
            p2.stack += self.pot // 2
            self.message = f"【決着】引き分け！（{name1}） ポットを分割しました。"
            
        self.pot = 0 # ポットを空にする

    def get_state(self):
        return {
            "phase": self.phase, "pot": self.pot, "current_turn": self.current_turn,
            "message": self.message, "community_cards": [c.to_dict() for c in self.community_cards],
            "players": [p.to_dict() for p in self.players.values()]
        }

game_instance = TexasHoldemEngine()

class PlayerAction(BaseModel):
    player_id: str
    action_type: str
    amount: int = 0

@app.post("/api/start")
def start_game():
    game_instance.start_new_hand()
    return {"status": "started", "game_state": game_instance.get_state()}

@app.post("/api/action")
def take_action(action: PlayerAction):
    game_instance.process_action(action.player_id, action.action_type, action.amount)
    return {"status": "success", "game_state": game_instance.get_state()}

@app.post("/api/reset")
def reset_game():
    game_instance.reset_game()
    return {"status": "reset", "game_state": game_instance.get_state()}