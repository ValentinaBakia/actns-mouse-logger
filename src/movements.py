from dataclasses import dataclass


@dataclass(frozen=True)
class DirectedMove:
    label: str
    start_anchor: str
    end_anchor: str


DIRECTED_MOVES: tuple[DirectedMove, ...] = (
    DirectedMove(label="TL->TR", start_anchor="TL", end_anchor="TR"),
    DirectedMove(label="TR->TL", start_anchor="TR", end_anchor="TL"),
    DirectedMove(label="BL->BR", start_anchor="BL", end_anchor="BR"),
    DirectedMove(label="BR->BL", start_anchor="BR", end_anchor="BL"),
    DirectedMove(label="TL->BL", start_anchor="TL", end_anchor="BL"),
    DirectedMove(label="BL->TL", start_anchor="BL", end_anchor="TL"),
    DirectedMove(label="TR->BR", start_anchor="TR", end_anchor="BR"),
    DirectedMove(label="BR->TR", start_anchor="BR", end_anchor="TR"),
    DirectedMove(label="TL->BR", start_anchor="TL", end_anchor="BR"),
    DirectedMove(label="BR->TL", start_anchor="BR", end_anchor="TL"),
    DirectedMove(label="TR->BL", start_anchor="TR", end_anchor="BL"),
    DirectedMove(label="BL->TR", start_anchor="BL", end_anchor="TR"),
)
