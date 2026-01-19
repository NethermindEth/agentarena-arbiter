"""
Prompts for deduplication and validation.
Standard prompts for LLM-based finding analysis.
"""

DEDUPLICATE_PROMPT = """You are an expert in smart-contract security. Your job: de-duplicate a list of security findings and provide a detailed report of kept and removed findings.

## **Task:**
- Read the "List of findings" (each item contains an `index` field).
- Treat two findings as duplicates if they describe the **same underlying issue** ***and*** affect the same function or code section.
- For every duplicate set, keep **one** finding - pick the one with the most complete/precise description - and discard the others.
- The returned list of indexes must contain the original `index` of the finding, not the position in the list.

## **Duplicate description:**
Use the following criteria to determine if two findings are duplicates:
- They come from the same function or section of code.
- They describe the same underlying security issue.
- They have similar description and consequences.

## **Output Format (strict):**
Return exactly one JSON object with these keys and no others. Do not include any text before or after the JSON. Do not use code fences or backticks.

Rules:
- "indexes": required. Unique integers of the original "index" values to KEEP (not list positions).
- "removed_duplicates": required. If no duplicates were removed, return [] but keep the key.

## **List of findings to de-duplicate:**
```json
{vulnerabilities}
```
"""

VALIDATION_PROMPT = """You are an expert smart contracts security researcher. Your task is to rigorously evaluate if a finding is a false positive or not, given the smart contract code and some additional context for reference.
The following finding has been detected of being of the following type: `{vulnerability_type}`, which refers to {vulnerability_type_description}.

### **`{vulnerability_type}` Validation Rules (if available):**
{vulnerability_type_mitigation}

---

To help you in your final decision, follow the step-by-step process below.

## **Step-by-step process:**

### Step 1: Technical Validity Check
**Definition:** Is the technical claim described in the finding actually present in the code? This includes checking if the vulnerability or behavior is implemented as described.
Is the finding technically valid, based on the contract code and your knowledge? Consider mathematical correctness, logical soundness, and syntactic accuracy. Browse internet for the compiler version used and some recent changes that you might not be aware of, or some external dependencies usage, etc. If the technical claim is partially valid (the cause is correct, but the impact is not), consider keeping the finding as it could hide other issues, and could still allow to consolidate the logic.
- If the finding is invalid, set `final_result` to `False` and stop here.
- If valid, continue to step 2.

### Step 2: Contextual Validity Check
**Definition:** Does the technical issue matter for this project, given its purpose and requirements?
Given the project summary and developer documentation, does the finding make sense in the context of the project? Consider the project's purpose, design, and any relevant documentation.
- If the finding is invalid, set `final_result` to `False` and stop here.
- If valid, continue to step 3.

### Step 3: Contextual Legitimacy Check
**Definition:** Is the issue a real problem or an expected/intended behavior? Only judge as "intentional/expected" if there is clear, explicit evidence (e.g., code comments, documentation, or requirements) stating so. Do not assume intentionality from the absence of evidence.
At this point, we have already assessed that the finding was valid. But, is the finding a legitimate concern, or is it an intentional feature of the contract's logic or business purpose? And if it is an intentional feature, is it correctly implemented?
For example, if pausing a contract disables withdrawals, this is likely by design, not a malfunctional problem, since this is the whole point of a pause function.
- If the finding is not a genuine concern (i.e., a known feature or expected behavior), set `final_result` to `False` and stop here.
- If valid, set `final_result` to `True` and stop here.

## **Instructions:**
- Read the entire prompt and context carefully.
- Work through the step by step process.
- For each step, provide a quick distinct reasoning explanation based on the specific criteria of that step for your decision and the step result as a boolean.
- If a finding is about best practices or lack of defense-in-depth, keep the finding unless there is an explicit, project-level reason not to.
- In doubt, or if you lack information, never assume anything and **always keep the finding**.
- If you answer `True` to all steps, set `final_result` to `True`. Otherwise, set `final_result` to `False`.

## **Output Format:**
Return exactly one JSON object with two keys: `steps` and `final_result`, without any additional text, comments, explanations, backticks, or chain of thought. If you cannot reach a confident decision, set `final_result` to true.
- `steps`: An array of objects, where each object represents a step in the validation process and contains a `reasoning` (string) and a `step_result` (boolean).
- `final_result`: A boolean indicating the final decision on whether to keep the finding.

---

## **Finding to evaluate:**
```json
{vulnerabilities}
```

---

## **Finding hypothesis (if available):**
```json
{hypothesis}
```

## **Project Summary (if available):**
{summary}

## **Developer Documentation (if available):**
{dev_doc}

## Initial reasoning behind the finding's validation (if available):
```json
{reasoning_steps}
```

## **Contract Code for context:**
```{contract_language}
{contract_code}
```
"""

