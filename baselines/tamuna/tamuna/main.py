"""Run CNN federated learning for MNIST dataset."""
import os
from typing import List

import flwr as fl
import hydra
import numpy as np
import torch.random
from flwr.server.history import History
from hydra.core.hydra_config import HydraConfig
from omegaconf import DictConfig, OmegaConf
from torch.utils.data import DataLoader

import tamuna.client as client
import tamuna.server as server
from tamuna.dataset import load_datasets
from tamuna.strategy import CentralizedFedAvgStrategy, TamunaStrategy
from tamuna.utils import compare_histories, save_results_as_pickle


@hydra.main(config_path="conf", config_name="base", version_base=None)
def main(cfg: DictConfig) -> None:
    """Run CNN federated learning on MNIST.

    Parameters
    ----------
    cfg : DictConfig
        An omegaconf object that stores the hydra config.
    """
    # print config structured as YAML
    print(OmegaConf.to_yaml(cfg))

    # Hydra automatically creates an output directory
    # Let's retrieve it and save some results there
    save_path = HydraConfig.get().runtime.output_dir
    with open(f"{save_path}/config.yaml", "wt") as f:
        OmegaConf.save(cfg, f)

    # partition dataset and get dataloaders
    trainloaders, testloader = load_datasets(
        num_clients=cfg.server.num_clients, iid=cfg.dataset.iid
    )

    tamuna_histories = run_tamuna(cfg, save_path, testloader, trainloaders)
    fedavg_histories = run_fedavg(cfg, save_path, testloader, trainloaders)

    with open("model_dim.txt", "rt") as f:
        dim = int(f.readline())

    compare_histories(tamuna_histories, fedavg_histories, dim, save_path, cfg)


def run_fedavg(
    cfg: DictConfig,
    save_path: str,
    testloader: DataLoader,
    trainloaders: List[DataLoader],
) -> List[History]:
    """Run FedAvg.

    Parameters
    ----------
    cfg : DictConfig
        An omegaconf object that stores the hydra config.
    save_path: str
         Path where to save the results of the training.
    testloader: DataLoader
        Dataloader contaning test split for centralized evaluation
    trainloaders: List[DataLoader]
        Dataloaders for clients

    Returns
    -------
    List[History]
        Histories of every run (based of n_repeats).
    """
    # prepare function that will be used to spawn each client
    client_fn = client.gen_fedavg_client_fn(
        trainloaders=trainloaders,
        lr=cfg.client.learning_rate,
        model=cfg.model,
        client_device=cfg.client.client_device,
    )

    # using only central evaluation
    evaluate_fn = server.gen_evaluate_fn(
        testloader, device=cfg.server.server_device, model=cfg.model
    )

    histories = []
    for i in range(cfg.meta.n_repeats):
        np.random.seed(cfg.meta.seed)
        torch.manual_seed(cfg.meta.seed)

        # Start simulation
        history = fl.simulation.start_simulation(
            client_fn=client_fn,
            num_clients=cfg.server.num_clients,
            config=fl.server.ServerConfig(num_rounds=cfg.server.num_rounds),
            client_resources={
                "num_cpus": cfg.client.client_resources.num_cpus,
                "num_gpus": cfg.client.client_resources.num_gpus,
            },
            strategy=CentralizedFedAvgStrategy(
                clients_per_round=cfg.server.clients_per_round,
                epochs_per_round=int(1 / cfg.server.p),
                evaluate_fn=evaluate_fn,
            ),
        )

        # save results as a Python pickle using a file_path
        # the directory created by Hydra
        save_results_as_pickle(history, file_path=f"{save_path}/fedavg_results_{i}.pkl")

        histories.append(history)

    return histories


def run_tamuna(
    cfg: DictConfig,
    save_path: str,
    testloader: DataLoader,
    trainloaders: List[DataLoader],
):
    """Run Tamuna.

    Parameters
    ----------
    cfg : DictConfig
        An omegaconf object that stores the hydra config.
    save_path: str
         Path where to save the results of the training.
    testloader: DataLoader
        Dataloader contaning test split for centralized evaluation
    trainloaders: List[DataLoader]
        Dataloaders for clients

    Returns
    -------
    List[History]
        Histories of every run (based of n_repeats).
    """
    # prepare function that will be used to spawn each client
    client_fn = client.gen_tamuna_client_fn(
        trainloaders=trainloaders,
        learning_rate=cfg.client.learning_rate,
        model=cfg.model,
        client_device=cfg.client.client_device,
    )

    # using only central evaluation
    evaluate_fn = server.gen_evaluate_fn(
        testloader, device=cfg.server.server_device, model=cfg.model
    )

    histories = []
    for i in range(cfg.meta.n_repeats):
        np.random.seed(cfg.meta.seed)
        torch.manual_seed(cfg.meta.seed)

        # remove possible previous client states
        if os.path.exists(client.TamunaClient.STATE_DIR):
            for filename in os.listdir(client.TamunaClient.STATE_DIR):
                if filename.endswith("_state.bin"):
                    os.remove(f"{client.TamunaClient.STATE_DIR}/{filename}")
        else:
            os.mkdir(client.TamunaClient.STATE_DIR)

        # number of epochs per round is determined by probability p
        epochs_per_round = np.random.geometric(
            p=cfg.server.p, size=cfg.server.num_rounds
        ).tolist()

        # Start simulation
        history = fl.simulation.start_simulation(
            client_fn=client_fn,
            num_clients=cfg.server.num_clients,
            config=fl.server.ServerConfig(num_rounds=cfg.server.num_rounds),
            client_resources={
                "num_cpus": cfg.client.client_resources.num_cpus,
                "num_gpus": cfg.client.client_resources.num_gpus,
            },
            strategy=TamunaStrategy(
                clients_per_round=cfg.server.clients_per_round,
                epochs_per_round=epochs_per_round,
                eta=cfg.client.eta,
                s=cfg.server.s,
                evaluate_fn=evaluate_fn,
            ),
        )

        # save results as a Python pickle using a file_path
        # the directory created by Hydra
        save_results_as_pickle(history, file_path=f"{save_path}/tamuna_results_{i}.pkl")

        histories.append(history)

    return histories


if __name__ == "__main__":
    main()
