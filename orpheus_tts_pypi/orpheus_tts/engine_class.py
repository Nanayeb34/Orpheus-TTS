import asyncio
import torch
import os
from vllm import AsyncLLMEngine, AsyncEngineArgs, SamplingParams
from transformers import AutoTokenizer
import threading
import queue
import pickle
from .decoder import tokens_decoder_sync
transcripts={"darlington_1":"A math verifier. So let's say a calculator. You will keep trying things and then using a calculator to check if it's correct. Based on that, it can improve and become superhuman. We saw this in chess. When an AI system beat humans in chess in like what the 90s and since then, no one has been able to beat AI systems. We've seen this in go. It's going to happen in every field. So human beings are here and we never seem to change, but AI step every couple of months is doing this...",
             "darlington_2":"Sure. So um I grew up in Ghana. I grew up all over Ghana actually. So growing up, I was all over the place, but there were two things that I think I really enjoyed. So I enjoyed drawing anything that has to do with art I was into. But I also enjoyed destroying things <laugh>. So, and especially electronic devices. um I think I blew up a couple of TVs, almost burned down the house the house once, and um..."}
class OrpheusModel:
    def __init__(self, model_name, dtype=torch.bfloat16, tokenizer='canopylabs/orpheus-3b-0.1-pretrained', **engine_kwargs):
        self.model_name = self._map_model_params(model_name)
        self.dtype = dtype
        self.engine_kwargs = engine_kwargs  # vLLM engine kwargs
        self.engine = self._setup_engine()
        self.available_voices = ["zoe", "zac","jess", "leo", "mia", "julia", "leah"]
        
        # Use provided tokenizer path or default to model_name
        tokenizer_path = tokenizer if tokenizer else model_name
        self.tokenizer = self._load_tokenizer(tokenizer_path)

    def _load_tokenizer(self, tokenizer_path):
        """Load tokenizer from local path or HuggingFace hub"""
        try:
            # Check if tokenizer_path is a local directory
            if os.path.isdir(tokenizer_path):
                return AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
            else:
                return AutoTokenizer.from_pretrained(tokenizer_path)
        except Exception as e:
            print(f"Error loading tokenizer: {e}")
            print(f"Falling back to default tokenizer")
            return AutoTokenizer.from_pretrained("gpt2")
    
    def _map_model_params(self, model_name):
        model_map = {
            # "nano-150m":{
            #     "repo_id": "canopylabs/orpheus-tts-0.1-finetune-prod",
            # }, 
            # "micro-400m":{
            #     "repo_id": "canopylabs/orpheus-tts-0.1-finetune-prod",
            # }, 
            # "small-1b":{
            #     "repo_id": "canopylabs/orpheus-tts-0.1-finetune-prod",
            # },
            "medium-3b":{
                "repo_id": "canopylabs/orpheus-tts-0.1-finetune-prod",
            },
        }
        unsupported_models = ["nano-150m", "micro-400m", "small-1b"]
        if (model_name  in unsupported_models):
            raise ValueError(f"Model {model_name} is not supported. Only medium-3b is supported, small, micro and nano models will be released very soon")
        elif model_name in model_map:
            return model_name[model_name]["repo_id"]
        else:
            return model_name
        
    def _setup_engine(self):
        engine_args = AsyncEngineArgs(
            model=self.model_name,
            dtype=self.dtype,
            **self.engine_kwargs
        )
        
        return AsyncLLMEngine.from_engine_args(engine_args)
    
    def validate_voice(self, voice):
        if voice:
            if voice not in self.engine.available_voices:
                raise ValueError(f"Voice {voice} is not available for model {self.model_name}")
    
    def _format_prompt(self, prompt, voice="tara", model_type="larger"):
        if model_type == "smaller":
            if voice:
                return f"<custom_token_3>{prompt}[{voice}]<custom_token_4><custom_token_5>"
            else:
                return f"<custom_token_3>{prompt}<custom_token_4><custom_token_5>"
        else:
            if 'darlington' in voice:
                number= voice.split('_')[-1]
                print(f"using darlington voice {number}")
                with open(f'darlington_{number}.pkl','rb') as f:
                    myts=pickle.load(f)
                    ref_transcript=transcripts[f'darlington_{number}']
                start_token = torch.tensor([[ 128259]], dtype=torch.int64)
                end_tokens = torch.tensor([[128009, 128260, 128261, 128257]], dtype=torch.int64)   
                final_token=torch.tensor([[128258,128262]],dtype=torch.int64) 


                adapted_prompt=self.tokenizer(ref_transcript,return_tensors="pt")
                zero_prompt_input_ids=torch.cat([start_token,adapted_prompt['input_ids'],end_tokens,torch.tensor([myts]),final_token],dim=1)
                
                input_ids=self.tokenizer(prompt,return_tensors="pt").input_ids
                all_input_ids = torch.cat([zero_prompt_input_ids,start_token, input_ids, end_tokens], dim=1)
                prompt_string = self.tokenizer.decode(all_input_ids[0])
                return prompt_string
            
            elif 'darlington' not in voice:
                adapted_prompt = f"{voice}: {prompt}"
                prompt_tokens = self.tokenizer(adapted_prompt, return_tensors="pt")
                start_token = torch.tensor([[ 128259]], dtype=torch.int64)
                end_tokens = torch.tensor([[128009, 128260, 128261, 128257]], dtype=torch.int64)
                all_input_ids = torch.cat([start_token, prompt_tokens.input_ids, end_tokens], dim=1)
                prompt_string = self.tokenizer.decode(all_input_ids[0])
                return prompt_string
            elif voice==None:
                prompt_tokens = self.tokenizer(prompt, return_tensors="pt")
                start_token = torch.tensor([[ 128259]], dtype=torch.int64)
                end_tokens = torch.tensor([[128009, 128260, 128261, 128257]], dtype=torch.int64)
                all_input_ids = torch.cat([start_token, prompt_tokens.input_ids, end_tokens], dim=1)
                prompt_string = self.tokenizer.decode(all_input_ids[0])
                return prompt_string

 


    def generate_tokens_sync(self, prompt, voice=None, request_id="req-001", temperature=0.6, top_p=0.8, max_tokens=1200, stop_token_ids = [49158], repetition_penalty=1.3):
        prompt_string = self._format_prompt(prompt, voice)
        sampling_params = SamplingParams(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,  # Adjust max_tokens as needed.
        stop_token_ids = stop_token_ids, 
        repetition_penalty=repetition_penalty, 
        )

        token_queue = queue.Queue()

        async def async_producer():
            if 'darlington' in voice:
                async for result in self.engine.generate(input_ids=prompt_string, sampling_params=sampling_params, request_id=request_id):
                    # Place each token text into the queue.
                    token_queue.put(result.outputs[0].text)
            else:
                async for result in self.engine.generate(prompt=prompt_string, sampling_params=sampling_params, request_id=request_id):
                    # Place each token text into the queue.
                    token_queue.put(result.outputs[0].text)
            token_queue.put(None)  # Sentinel to indicate completion.

        def run_async():
            asyncio.run(async_producer())

        thread = threading.Thread(target=run_async)
        thread.start()

        while True:
            token = token_queue.get()
            if token is None:
                break
            yield token

        thread.join()
    
    def generate_speech(self, **kwargs):
        return tokens_decoder_sync(self.generate_tokens_sync(**kwargs))


