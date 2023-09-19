"""Tamuna and FedAvg strategies."""

from typing import Callable, List

import flwr.common
import numpy as np
import torch
from flwr.common import FitIns, NDArrays, ndarrays_to_parameters, parameters_to_ndarrays
from flwr.server.strategy import Strategy

from tamuna.models import Net


def aggregate(weights: List[NDArrays], s: float) -> NDArrays:
    """Compute average of the clients' weights."""
    averaged_weights = [
        np.sum(layer_updates, axis=0) / s for layer_updates in zip(*weights)
    ]
    return averaged_weights


def create_pattern(dim: int, cohort_size: int, sparsity: int):
    return torch.ones(size=(dim, cohort_size))

    """Create compression pattern."""
    q = torch.zeros(size=(dim, cohort_size))
    if dim >= cohort_size / sparsity:
        k = 0
        for i in range(dim):
            for _j in range(sparsity):
                q[i, k] = 1
                k = (k + 1) % cohort_size
    else:
        k = 0
        for _j in range(sparsity):
            for i in range(dim):
                q[i, k] = 1
                k += 1

    return q


def shuffle_columns(q: torch.Tensor):
    """Shuffle the columns of the compression pattern."""
    q = q[:, torch.randperm(q.size()[1])]
    return q


class TamunaStrategy(Strategy):
    """Tamuna Strategy with control variates and compression."""

    def __init__(
        self,
        clients_per_round: int,
        epochs_per_round: List[int],
        eta: float,
        s: int,
        evaluate_fn: Callable,
    ) -> None:
        self.clients_per_round = clients_per_round
        self.epochs_per_round = epochs_per_round
        self.evaluate_fn = evaluate_fn
        self.eta = eta
        self.s = s
        self.dim = None
        self.server_model = None
        self.compression_pattern = None

    def initialize_parameters(self, client_manager):
        """Initialize the server model."""
        self.server_model = Net()
        self.dim = sum(p.numel() for p in self.server_model.parameters())

        with open("model_dim.txt", "wt") as f:
            f.write(str(self.dim))

        self.compression_pattern = create_pattern(
            self.dim, self.clients_per_round, self.s
        )  # dim x cohort_size

        ndarrays = [
            val.cpu().numpy() for _, val in self.server_model.state_dict().items()
        ]
        return flwr.common.ndarrays_to_parameters(ndarrays)

    def configure_fit(self, server_round: int, parameters, client_manager):
        """Sample clients and create compression pattern for each of them."""
        sampled_clients = client_manager.sample(self.clients_per_round)
        client_fit_ins = []

        self.compression_pattern = shuffle_columns(self.compression_pattern)

        for i in range(self.clients_per_round):
            config = {
                "epochs": self.epochs_per_round[server_round - 1],
                "eta": self.eta,
                "mask": self.compression_pattern[:, i],
                "round_id": server_round
            }
            client_fit_ins.append(FitIns(parameters, config))

        return [(client, client_fit_ins[i]) for i, client in enumerate(sampled_clients)]

    def aggregate_fit(self, server_round, results, failures):
        """Average the clients' weights."""
        weights = [parameters_to_ndarrays(fit_res.parameters) for _, fit_res in results]
        parameters_aggregated = ndarrays_to_parameters(aggregate(weights, self.s))
        return parameters_aggregated, {}

    def configure_evaluate(self, server_round, parameters, client_manager):
        """Not used."""
        return []

    def aggregate_evaluate(self, server_round, results, failures):
        """Not used."""
        return None, {}

    def evaluate(self, server_round: int, parameters):
        """Centralized evaluation."""
        return self.evaluate_fn(parameters)


class CentralizedFedAvgStrategy(Strategy):
    """FedAvg Strategy with centralized evaluation."""

    def __init__(
        self,
        clients_per_round: int,
        epochs_per_round: int,
        evaluate_fn: Callable,
    ) -> None:
        self.clients_per_round = clients_per_round
        self.evaluate_fn = evaluate_fn
        self.epochs_per_round = epochs_per_round
        self.dim = None
        self.server_model = None

    def initialize_parameters(self, client_manager):
        """Initialize the server model."""
        self.server_model = Net()

        self.dim = sum(p.numel() for p in self.server_model.parameters())

        ndarrays = [
            val.cpu().numpy() for _, val in self.server_model.state_dict().items()
        ]
        return flwr.common.ndarrays_to_parameters(ndarrays)

    def configure_fit(self, server_round: int, parameters, client_manager):
        """Sample clients and create compression pattern for each of them."""
        sampled_clients = client_manager.sample(self.clients_per_round)
        config = {"epochs": self.epochs_per_round}
        return [(client, FitIns(parameters, config)) for client in sampled_clients]

    def aggregate_fit(self, server_round, results, failures):
        """Average the clients' weights."""
        weights = [parameters_to_ndarrays(fit_res.parameters) for _, fit_res in results]
        parameters_aggregated = ndarrays_to_parameters(
            aggregate(weights, self.clients_per_round)
        )
        return parameters_aggregated, {}

    def configure_evaluate(self, server_round, parameters, client_manager):
        """Not used."""
        return []

    def aggregate_evaluate(self, server_round, results, failures):
        """Not used."""
        return None, {}

    def evaluate(self, server_round: int, parameters):
        """Centralized evaluation."""
        return self.evaluate_fn(parameters)
