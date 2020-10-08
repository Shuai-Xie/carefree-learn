import torch

import numpy as np
import torch.nn as nn

from typing import *
from cfdata.types import np_int_type

from .auxiliary import *
from ..misc.toolkit import *


class Linear(nn.Module):
    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        *,
        bias: bool = True,
        pruner_config: dict = None,
        init_method: Union[str, None] = "xavier_uniform",
        **kwargs,
    ):
        super().__init__()
        self.linear = nn.Linear(in_dim, out_dim, bias)
        pruner = None if pruner_config is None else Pruner(pruner_config)
        self.config, self.pruner = kwargs, pruner
        self._use_bias, self._init_method = bias, init_method
        with torch.no_grad():
            self.reset_parameters()

    @property
    def weight(self) -> torch.Tensor:
        return self.linear.weight

    @property
    def bias(self) -> Union[torch.Tensor, None]:
        return self.linear.bias

    def forward(self, net):
        weight = (
            self.linear.weight
            if self.pruner is None
            else self.pruner(self.linear.weight)
        )
        return nn.functional.linear(net, weight, self.linear.bias)

    def reset_parameters(self):
        if self._init_method is None:
            return
        if self._init_method not in Initializer.defined_initialization:
            return
        initializer = Initializer(self.config.setdefault("initialize_config", {}))
        initializer.initialize(self.linear.weight, self._init_method)
        bias_fill = self.config.setdefault("bias_fill", 0.0)
        if self._use_bias:
            self.linear.bias.data.fill_(bias_fill)


class Mapping(nn.Module):
    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        *,
        bias: bool = None,
        pruner_config: dict = None,
        dropout: float = 0.5,
        batch_norm: bool = True,
        activation: str = "ReLU",
        init_method: str = "xavier_uniform",
        **kwargs,
    ):
        super().__init__()
        self.config = kwargs
        if bias is None:
            bias = not batch_norm
        self.linear = Linear(
            in_dim,
            out_dim,
            bias=bias,
            pruner_config=pruner_config,
            init_method=init_method,
            **kwargs,
        )
        self.bn = None if not batch_norm else BN(out_dim)
        if activation is None:
            self.activation = None
        else:
            activations_ins = Activations(
                self.config.setdefault("activation_config", None)
            )
            self.activation = activations_ins.module(activation)
        use_dropout = 0.0 < dropout < 1.0
        self.dropout = None if not use_dropout else Dropout(dropout)

    @property
    def weight(self) -> torch.Tensor:
        return self.linear.weight

    @property
    def bias(self) -> Union[torch.Tensor, None]:
        return self.linear.bias

    def forward(self, net, *, reuse: bool = False):
        net = self.linear(net)
        if self.bn is not None:
            net = self.bn(net)
        if self.activation is not None:
            net = self.activation(net)
        if self.dropout is not None:
            net = self.dropout(net, reuse=reuse)
        return net


class MLP(nn.Module):
    def __init__(
        self,
        in_dim: int,
        out_dim: Union[int, None],
        num_units: List[int],
        mapping_configs: List[Dict[str, Any]],
        *,
        final_mapping_config: Dict[str, Any] = None,
    ):
        super().__init__()
        mappings = []
        for num_unit, mapping_config in zip(num_units, mapping_configs):
            mappings.append(Mapping(in_dim, num_unit, **mapping_config))
            in_dim = num_unit
        if out_dim is not None:
            if final_mapping_config is None:
                final_mapping_config = {}
            mappings.append(Linear(in_dim, out_dim, **final_mapping_config))
        self.mappings = nn.ModuleList(mappings)

    @property
    def weights(self) -> List[torch.Tensor]:
        return [mapping.weight for mapping in self.mappings]

    @property
    def biases(self) -> List[Union[torch.Tensor, None]]:
        return [mapping.bias for mapping in self.mappings]

    def forward(self, net):
        for mapping in self.mappings:
            net = mapping(net)
        return net


class DNDF(nn.Module):
    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        *,
        num_tree: int = 10,
        tree_depth: int = 4,
        tree_proj_config: dict = None,
        output_type: str = "output",
        is_regression: bool = None,
    ):
        super().__init__()
        self._num_tree, self._tree_depth, self._output_type = (
            num_tree,
            tree_depth,
            output_type,
        )
        self._is_regression = out_dim == 1 if is_regression is None else is_regression
        self._num_leaf = 2 ** (self._tree_depth + 1)
        self._num_internals = self._num_leaf - 1
        self._output_dim = out_dim
        tree_proj_config = self._setup_tree_proj(tree_proj_config)
        self.tree_proj = Linear(
            in_dim, self._num_internals * self._num_tree, **tree_proj_config
        )
        self.leaves = nn.Parameter(
            torch.empty(self._num_tree, self._num_leaf, self._output_dim)
        )
        torch.nn.init.xavier_uniform_(self.leaves.data)
        # masks
        num_repeat, num_local_internals = self._num_leaf // 2, 1
        increment_masks = [
            torch.from_numpy(
                np.repeat([0, self._num_internals], num_repeat).astype(np_int_type)
            )
        ]
        for _ in range(1, self._tree_depth + 1):
            num_repeat //= 2
            num_local_internals *= 2
            increment_mask = np.repeat(
                np.arange(num_local_internals - 1, 2 * num_local_internals - 1), 2
            )
            increment_mask += np.tile([0, self._num_internals], num_local_internals)
            increment_mask = np.repeat(increment_mask, num_repeat)
            increment_mask = torch.from_numpy(increment_mask.astype(np_int_type))
            increment_masks.append(increment_mask)
        self.register_buffer("increment_masks", torch.stack(increment_masks))

    @staticmethod
    def _setup_tree_proj(tree_proj_config):
        if tree_proj_config is None:
            tree_proj_config = {}
        pruner_config = tree_proj_config.pop("pruner_config", {})
        if pruner_config is not None:
            tree_proj_config["pruner"] = Pruner(pruner_config)
        return tree_proj_config

    def forward(self, net):
        device = net.device
        tree_net = self.tree_proj(net)
        p_left = torch.split(torch.sigmoid(tree_net), self._num_internals, dim=-1)
        flat_probabilities = [
            torch.reshape(torch.cat([p, 1.0 - p], dim=-1), [-1]) for p in p_left
        ]
        num_flat_prob = 2 * self._num_internals
        arange = torch.arange(0, num_flat_prob * net.shape[0], num_flat_prob)
        batch_indices = torch.reshape(arange, [-1, 1]).to(device)
        routes = [
            p_flat.take(batch_indices + self.increment_masks[0])
            for p_flat in flat_probabilities
        ]
        for i in range(1, self._tree_depth + 1):
            for j, p_flat in enumerate(flat_probabilities):
                routes[j] *= p_flat.take(batch_indices + self.increment_masks[i])
        leaves, features = self.leaves, torch.cat(routes, 1)
        if not self._is_regression and self._output_dim > 1:
            leaves = nn.functional.softmax(leaves, dim=-1)
        leaves = leaves.view(self._num_tree * self._num_leaf, self._output_dim)
        return features.matmul(leaves) / self._num_tree

    def reset_parameters(self):
        self.tree_proj.reset_parameters()
        nn.init.xavier_uniform_(self.leaves.data)


__all__ = ["Linear", "Mapping", "MLP", "DNDF"]
