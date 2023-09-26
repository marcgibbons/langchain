import asyncio
from tqdm import tqdm
from langchain.cache import SQLiteCache
from dotenv import load_dotenv
from datasets import load_dataset
import langchain
from langchain.prompts.chat import ChatPromptTemplate
from langchain.chains import LLMChain
from langchain.chat_models.openai import ChatOpenAI
from langchain.pydantic_v1 import BaseModel
from langchain.output_parsers.json import SimpleJsonOutputParser
from langchain.evaluation.comparison import PairwiseStringEvalChain
from langchain.callbacks.manager import get_openai_callback
    
class SummaryParser(SimpleJsonOutputParser):

    def parse(self, text: str) -> str:
        raw_json = super().parse(text)
        return raw_json[-1]["Denser_Summary"]

    @property
    def _type(self) -> str:
        return "summary_parser"

dataset = load_dataset("griffin/chain_of_density", "unannotated")

load_dotenv()

llm = ChatOpenAI(temperature=0, model="gpt-4-0613", max_retries=1000)

ft_llm = ChatOpenAI(temperature=0, model="ft:gpt-3.5-turbo-0613:personal:cod-summarization:82oPBKod", max_retries=1000)

class Sample(BaseModel):
    article: str
    starting_summary: str
    final_summary: str


samples: list[Sample] = []

for sample in dataset["train"]:
    samples.append(
        Sample(
            article=sample["article"],
            starting_summary=sample["prediction"][0],
            final_summary=sample["prediction"][-1],
        )
    )

PROMPT = """Article: {article}
You will generate increasingly concise, entity-dense summaries of the above article. 

Repeat the following 2 steps 5 times. 

Step 1. Identify 1-3 informative entities (";" delimited) from the article which are missing from the previously generated summary. 
Step 2. Write a new, denser summary of identical length which covers every entity and detail from the previous summary plus the missing entities. 

A missing entity is:
- relevant to the main story, 
- specific yet concise (5 words or fewer), 
- novel (not in the previous summary), 
- faithful (present in the article), 
- anywhere (can be located anywhere in the article).

Guidelines:

- The first summary should be long (4-5 sentences, ~80 words) yet highly non-specific, containing little information beyond the entities marked as missing. Use overly verbose language and fillers (e.g., "this article discusses") to reach ~80 words.
- Make every word count: rewrite the previous summary to improve flow and make space for additional entities.
- Make space with fusion, compression, and removal of uninformative phrases like "the article discusses".
- The summaries should become highly dense and concise yet self-contained, i.e., easily understood without the article. 
- Missing entities can appear anywhere in the new summary.
- Never drop entities from the previous summary. If space cannot be made, add fewer new entities. 

Remember, use the exact same number of words for each summary.
Answer in JSON. The JSON should be a list (length 5) of dictionaries whose keys are "Missing_Entities" and "Denser_Summary"."""  # noqa: E501

BASE_PROMPT = ChatPromptTemplate.from_template("""Article: {article}

Write a summary of the above article. Guidelines:

- The summary should be long (4-5 sentences, ~80 words) yet highly non-specific, containing little information beyond the entities marked as missing. Use overly verbose language and fillers (e.g., "this article discusses") to reach ~80 words.
- Make space with fusion, compression, and removal of uninformative phrases like "the article discusses".
- The summaries should become highly dense and concise yet self-contained, i.e., easily understood without the article.

Just give your summary and NOTHING else.""")

cod_summarization_prompt = ChatPromptTemplate.from_messages(
    ("human", PROMPT)
)

cod_summarize_chain = LLMChain(llm=llm, prompt=cod_summarization_prompt, output_parser=SummaryParser())

ft_summarize_chain = FT_PROMPT | ft_llm

evaluator = PairwiseStringEvalChain.from_llm(llm=llm)

def _reverse_verdict(verdict: str) -> str:
    return "Win" if verdict == "Loss" else "Loss" if verdict == "Win" else "Tie"

async def evaluate(sample: Sample) -> bool:
    base_summary = (await base_summarize_chaim.ainvoke({"article": sample.article})).content
    ft_summary = (await ft_summarize_chain.ainvoke({"article": sample.article})).content
    print("Base summary:", base_summary)
    print("FT summary:", ft_summary)
    reverse = (len(base_summary) + len(ft_summary)) % 2 == 0
    result = await evaluator.aevaluate_string_pairs(
        input=f"Give a summary of the following article:\n\n{sample.article}",
        prediction=sample.final_summary if not reverse else sample.starting_summary,
        prediction_b=sample.starting_summary if not reverse else sample.final_summary,
    )
    print(result)
    if reverse:
        return _reverse_verdict(result["verdict"])
    return result["verdict"]

async def main() -> None:
    pbar = tqdm(total=len(samples[:100]))
    sempahore = asyncio.Semaphore(10)

    async def boxed_evaluate(sample: Sample) -> str:
        with get_openai_callback() as cb:
            async with sempahore:
                results = await evaluate(sample)
                pbar.update(1)
                print("Total cost:", cb.total_cost)
                return results

    results = await asyncio.gather(
        *[boxed_evaluate(sample) for sample in samples[:100]]
    )

    results_excluding_ties = [result for result in results if result != "Tie"]
    print(
        "Win rate:",
        sum([result == "Win" for result in results]) / len(results_excluding_ties),
    )

if __name__ == "__main__":
    asyncio.run(main())


# N=100 With first and last summary
# Win rate: 80%