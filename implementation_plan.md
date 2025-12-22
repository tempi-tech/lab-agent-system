# Implementation Plan - Daily Reporter Output Refinement

The goal is to remove conversational filler (e.g., "はい、承知いたしました！") from the Daily Reporter's output and ensure it strictly follows the report format.

## Proposed Changes

### `src/agents/daily_reporter/logic.py`

#### [MODIFY] Update `EditorInChief` Instruction
- Add a strict rule to **forbid** conversational filler at the beginning of the response.
- Explicitly state that the output must start directly with the report title or content.
- Add a "Negative Constraint" section to the prompt.

```python
            instruction=f"""...
            ## 禁止事項 (Negative Constraints)
            - 「はい、承知しました」「レポートを作成します」などの前置きは**一切禁止**です。
            - 出力は必ず `📅 **今日のラボ日誌**` から始めてください。
            ..."""
```

## Verification Plan

### Manual Verification
1.  Run the bot locally using `python main.py`.
2.  Check the output in the Discord test channel.
3.  Verify that the message starts directly with the report content and does not contain any conversational filler.
