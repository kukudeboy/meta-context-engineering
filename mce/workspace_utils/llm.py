from langchain_openai import ChatOpenAI
from typing import List, Type, TypeVar, Union
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor
import asyncio
import os
import httpx

from dotenv import load_dotenv

load_dotenv(override=False)


T = TypeVar('T', bound=BaseModel)

MAX_CONCURRENCY = int(os.getenv("MCE_MAX_LLM_CONCURRENCY", "50"))
MAX_LLM_CALLS = int(os.getenv("MCE_MAX_LLM_CALLS", "100"))

def _create_llm(http_async_client: httpx.AsyncClient) -> ChatOpenAI:
    # Create clients in the calling loop; async HTTP clients cannot be shared across loops.
    return ChatOpenAI(
        model=os.getenv("MCE_MODEL", "qwen3.7-flash"),
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv(
            "DASHSCOPE_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        temperature=0,
        timeout=float(os.getenv("MCE_LLM_TIMEOUT", "120")),
        max_tokens=int(os.getenv("MCE_LLM_MAX_TOKENS", "1024")),
        http_async_client=http_async_client,
    )

class TextResponse(BaseModel):
    """Simple text response from LLM."""
    response: str = Field(description="The LLM's response text")

async def call_llm_async(
    prompts: List[str],
    schema: Type[BaseModel] = None,
) -> Union[List[str], List[T]]:
    """
    Call llms with plain text or validated structured output.
    
    Args:
        prompts: List of prompts to send to the LLM
        schema: Optional Pydantic BaseModel class defining the output structure
        
    Returns:
        List of strings when schema is None, otherwise instances of the schema class
        
    Raises:
        ValueError: If batch limit is exceeded
    """
    if len(prompts) > MAX_LLM_CALLS:
        raise ValueError(f"Number of prompts ({len(prompts)}) exceeds maximum allowed per batch ({MAX_LLM_CALLS})")
    
    if schema is not None:
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise TypeError("schema must be a Pydantic BaseModel subclass")
        method = os.getenv("MCE_LLM_STRUCTURED_METHOD", "function_calling")
        if method not in ("function_calling", "json_schema"):
            raise ValueError("MCE_LLM_STRUCTURED_METHOD must be function_calling or json_schema")
    # Own the async transport for this batch, including when called from another thread.
    async with httpx.AsyncClient() as http_client:
        llm = _create_llm(http_client)
        if schema is None:
            runnable = llm.with_retry(stop_after_attempt=3)
        else:
            runnable = llm.with_structured_output(schema, method=method).with_retry(stop_after_attempt=3)
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def process_single(prompt: str) -> Union[str, T]:
            """Process a single prompt with semaphore control."""
            async with semaphore:
                result = await runnable.ainvoke(prompt)
                if schema is None:
                    if not isinstance(result.content, str):
                        raise ValueError("Expected a plain text response from the model")
                    return result.content
                if isinstance(result, schema):
                    return result
                if isinstance(result, dict):
                    return schema.model_validate(result)
                raise ValueError("The model did not return a valid structured response")

        results = await asyncio.gather(*[process_single(prompt) for prompt in prompts])
        return results


def call_llm(
    prompts: Union[str, List[str]],
    schema: Type[BaseModel] = None,
) -> Union[str, List[str], BaseModel, List[BaseModel]]:
    """
    Synchronous wrapper for LLM calls. Supports both single and batch prompts.
    
    Args:
        prompts: Single prompt string or list of prompts
        schema: Optional Pydantic BaseModel class. If None, returns plain text responses.
        
    Returns:
        - If prompts is a string and schema is None: returns string
        - If prompts is a string and schema is provided: returns schema instance
        - If prompts is a list and schema is None: returns list of strings
        - If prompts is a list and schema is provided: returns list of schema instances
        
    Examples:
        # Simple text response
        response = call_llm("What is 2+2?")
        print(response)  # "4"
        
        # Batch text responses
        responses = call_llm(["What is 2+2?", "What is 3+3?"])
        print(responses)  # ["4", "6"]
        
        # Structured response
        class Analysis(BaseModel):
            pattern: str
            confidence: float
        
        result = call_llm("Analyze this...", schema=Analysis)
        print(result.pattern)
        
        # Batch structured responses
        results = call_llm(["Analyze A", "Analyze B"], schema=Analysis)
        for r in results:
            print(r.pattern)
    """
    is_single = isinstance(prompts, str)
    prompt_list = [prompts] if is_single else prompts
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        results = asyncio.run(call_llm_async(prompt_list, schema))
    else:
        # This synchronous API must wait; run its coroutine in a separate loop/thread.
        # Async callers can await call_llm_async directly to avoid blocking their loop.
        with ThreadPoolExecutor(max_workers=1) as executor:
            results = executor.submit(
                asyncio.run, call_llm_async(prompt_list, schema)
            ).result()
    return results[0] if is_single else results
