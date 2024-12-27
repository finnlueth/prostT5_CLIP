import torch
import torch.nn as nn
import typing as T
from transformers import (
    AutoModelForCausalLM,
    T5EncoderModel,
    PretrainedConfig,
    CLIPConfig,
    PreTrainedModel,
    modeling_utils,
)
from transformers.modeling_outputs import ModelOutput
from dataclasses import dataclass
from typing import Optional

from transformers.models.clip.modeling_clip import (
    contrastive_loss,
    clip_loss,
    _get_vector_norm,
)

from src.model.configuration_protein_clip import ProtT5CLIPConfig


def _switch_phi_padding_side(hidden_states, attention_mask):
    """
    Adjusts embeddings from Phi models to move meaningful tokens to the start.
    Args:
        hidden_states: tensor of shape (batch_size, seq_length, hidden_dim)
        attention_mask: tensor of shape (batch_size, seq_length)
    Returns:
        Adjusted hidden states with same shape but meaningful tokens at start
    """
    batch_size = hidden_states.shape[0]

    adjusted_hidden_states = []
    for i in range(batch_size):
        actual_length = attention_mask[i].sum().item()

        sequence_embeddings = hidden_states[i]

        meaningful_embeddings = sequence_embeddings[-actual_length:]
        padding_embeddings = sequence_embeddings[:-actual_length]

        adjusted_sequence = torch.cat([meaningful_embeddings, padding_embeddings])
        adjusted_hidden_states.append(adjusted_sequence)

    adjusted_hidden_states = torch.stack(adjusted_hidden_states)
    return adjusted_hidden_states


@dataclass
class ProteinTextOutput(ModelOutput):
    """
    Output type for protein-text models.

    Args:
        loss (`torch.FloatTensor` of shape `(1,)`, *optional*):
            Contrastive loss between protein and text embeddings.
        logits_per_protein (`torch.FloatTensor` of shape `(batch_size, batch_size)`):
            Similarity between each protein and all texts in the batch.
        logits_per_text (`torch.FloatTensor` of shape `(batch_size, batch_size)`):
            Similarity between each text and all proteins in the batch.
        protein_embeds (`torch.FloatTensor` of shape `(batch_size, hidden_size)`):
            Protein embeddings.
        text_embeds (`torch.FloatTensor` of shape `(batch_size, hidden_size)`):
            Text embeddings.
    """

    loss: Optional[torch.FloatTensor] = None
    logits_per_protein: Optional[torch.FloatTensor] = None
    logits_per_text: Optional[torch.FloatTensor] = None
    protein_embeds: Optional[torch.FloatTensor] = None
    text_embeds: Optional[torch.FloatTensor] = None
    protein_outputs: Optional[torch.FloatTensor] = None
    text_outputs: Optional[torch.FloatTensor] = None
    proj_protein_embeds: Optional[torch.FloatTensor] = None
    proj_text_embeds: Optional[torch.FloatTensor] = None


class ProtT5CLIP(PreTrainedModel):
    config_class = ProtT5CLIPConfig
    main_input_name = "input_ids_sequence"

    def __init__(self, config: ProtT5CLIPConfig):
        super().__init__(config=config)

        device_map = config.device if hasattr(config, "device") else "auto"

        self.model_llm, self.loading_info_llm = AutoModelForCausalLM.from_pretrained(
            pretrained_model_name_or_path=config.name_or_path_llm,
            device_map=device_map,
            output_loading_info=True,
            torch_dtype="auto",
            trust_remote_code=True,
        )

        llm_dtype = next(self.model_llm.parameters()).dtype

        self.model_plm, self.loading_info_plm = T5EncoderModel.from_pretrained(
            pretrained_model_name_or_path=config.name_or_path_plm,
            device_map=device_map,
            output_loading_info=True,
            torch_dtype=llm_dtype,
        )

        self.projection_dim = config.projection_dim
        self.protein_embed_dim = config.plm_config.hidden_size
        self.text_embed_dim = config.llm_config.hidden_size

        self.protein_projection = nn.Linear(self.protein_embed_dim, self.projection_dim, bias=False, dtype=llm_dtype)
        self.text_projection = nn.Linear(self.text_embed_dim, self.projection_dim, bias=False, dtype=llm_dtype)
        self.logit_scale = nn.Parameter(torch.tensor(config.logit_scale_init_value, dtype=llm_dtype))

        for name, init_func in modeling_utils.TORCH_INIT_FUNCTIONS.items():
            setattr(torch.nn.init, name, init_func)
        self.post_init()

    def encode_protein(
        self,
        protein_ids=None,
        protein_attention_mask=None,
    ):
        outputs = self.model_plm(
            input_ids=protein_ids,
            attention_mask=protein_attention_mask,
        )
        return outputs

    def encode_text(
        self,
        text_ids=None,
        text_attention_mask=None,
    ):
        outputs = self.model_llm(input_ids=text_ids, attention_mask=text_attention_mask, output_hidden_states=True)

        is_phi_model = any("phi" in name.lower() for name in self.model_llm.config.architectures)
        if is_phi_model:
            last_hidden = outputs.hidden_states[-1]
            adjusted_last_hidden = _switch_phi_padding_side(hidden_states=last_hidden, attention_mask=text_attention_mask)
            outputs.hidden_states = tuple(list(outputs.hidden_states[:-1]) + [adjusted_last_hidden])
        return outputs

    def forward(
        self,
        input_ids_sequence: Optional[torch.LongTensor] = None,
        input_ids_text: Optional[torch.LongTensor] = None,
        attention_mask_sequence: Optional[torch.Tensor] = None,
        attention_mask_text: Optional[torch.Tensor] = None,
        return_loss: Optional[bool] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
        **kwargs,
    ):
        # print("------------------------------- forward -------------------------------")
        # print("input_ids_sequence", input_ids_sequence)
        # print("input_ids_text", input_ids_text)
        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict
        
        logits_per_protein = None
        logits_per_text = None
        protein_embeds = None
        text_embeds = None
        protein_outputs = None
        text_outputs = None

        if input_ids_sequence is not None:
            protein_outputs = self.encode_protein(
                protein_ids=input_ids_sequence,
                protein_attention_mask=attention_mask_sequence,
            )
            protein_embeds = protein_outputs["last_hidden_state"]
            proj_protein_embeds = self.protein_projection(protein_embeds)
        else:
            protein_outputs = None
            protein_embeds = None
            proj_protein_embeds = None

        if input_ids_text is not None:
            text_outputs = self.encode_text(
                text_ids=input_ids_text,
                text_attention_mask=attention_mask_text,
            )
            text_embeds = text_outputs["hidden_states"][-1]
            proj_text_embeds = self.text_projection(text_embeds)
        else:
            text_outputs = None
            text_embeds = None
            proj_text_embeds = None

        # TODO: check if this is needed or ask somebody about it
        # if attention_mask is not None:
        #     protein_embeds = protein_embeds * attention_mask["attention_mask_sequence"].unsqueeze(-1)
        #     text_embeds = text_embeds * attention_mask["attention_mask_text"].unsqueeze(-1)

        loss = None
        if proj_text_embeds is not None and proj_protein_embeds is not None:
            proj_protein_embeds = torch.mean(proj_protein_embeds, dim=1)
            proj_text_embeds = torch.mean(proj_text_embeds, dim=1)

            proj_protein_embeds = proj_protein_embeds / _get_vector_norm(proj_protein_embeds)
            proj_text_embeds = proj_text_embeds / _get_vector_norm(proj_text_embeds)

            logit_scale = self.logit_scale.exp()
            logits_per_text = torch.matmul(
                proj_text_embeds, proj_protein_embeds.t().to(proj_text_embeds.device)
            ) * logit_scale.to(proj_text_embeds.device)
            logits_per_protein = logits_per_text.t()

            if input_ids_sequence is not None and input_ids_text is not None:
                loss = clip_loss(logits_per_text)

        if not return_dict:
            output = (logits_per_protein, logits_per_text, text_embeds, protein_embeds, text_outputs, protein_outputs)
            return ((loss,) + output) if loss is not None else output
        return ProteinTextOutput(
            loss=loss,
            logits_per_protein=logits_per_protein,
            logits_per_text=logits_per_text,
            protein_embeds=protein_embeds,
            text_embeds=text_embeds,
            protein_outputs=protein_outputs,
            text_outputs=text_outputs,
            proj_protein_embeds=proj_protein_embeds,
            proj_text_embeds=proj_text_embeds,
        )
