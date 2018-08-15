"""von-Mises-Fisher complex-Angular-Centric-Gaussian mixture model

This is a specific mixture model to integrate DC and spatial observations. It
does and will not support independent dimensions.

This also explains, why concrete variable names (i.e. F, T, embedding) are used.
"""
from operator import xor
from dataclasses import dataclass

import numpy as np

from dc_integration.distribution import VonMisesFisher
from dc_integration.distribution import VonMisesFisherTrainer
from dc_integration.distribution import (
    ComplexAngularCentralGaussian,
    ComplexAngularCentralGaussianTrainer,
)
from dc_integration.distribution.utils import _ProbabilisticModel


@dataclass
class VMFCACGMM(_ProbabilisticModel):
    weight: np.array  # (K,)
    vmf: VonMisesFisher
    cacg: ComplexAngularCentralGaussian

    def predict(self, observation, embedding):
        assert np.iscomplexobj(observation), observation.dtype
        assert np.isrealobj(embedding), embedding.dtype
        observation = observation / np.maximum(
            np.linalg.norm(observation, axis=-1, keepdims=True),
            np.finfo(observation.dtype).tiny,
        )
        embedding = embedding / np.maximum(
            np.linalg.norm(embedding, axis=-1, keepdims=True),
            np.finfo(embedding.dtype).tiny
        )
        affiliation, quadratic_form = self._predict(observation, embedding)
        return affiliation

    def _predict(self, observation, embedding):
        F, T, D = observation.shape
        _, _, E = embedding.shape
        num_classes = self.weight.shape[-1]

        observation_ = observation[..., None, :, :]
        cacg_log_pdf, quadratic_form = self.cacg._log_pdf(observation_)

        embedding_ = np.reshape(embedding, (1, F * T, E))
        vmf_log_pdf = self.vmf.log_pdf(embedding_)
        vmf_log_pdf = np.transpose(
            np.reshape(vmf_log_pdf, (num_classes, F, T)), (1, 0, 2)
        )

        affiliation = (
            np.log(self.weight)[..., :, None]
            + cacg_log_pdf
            + vmf_log_pdf
        )
        affiliation -= np.max(affiliation, axis=-2, keepdims=True)
        np.exp(affiliation, out=affiliation)
        denominator = np.maximum(
            np.einsum("...kn->...n", affiliation)[..., None, :],
            np.finfo(affiliation.dtype).tiny,
        )
        affiliation /= denominator
        return affiliation, quadratic_form


class GCACGMMTrainer:
    def fit(
        self,
        observation,
        embedding,
        initialization=None,
        num_classes=None,
        iterations=100,
        saliency=None,
        min_concentration=1e-10,
        max_concentration=500,
        hermitize=True,
        trace_norm=True,
        eigenvalue_floor=1e-10,
        covariance_type="spherical",
    ) -> VMFCACGMM:
        """

        Args:
            observation: Shape (F, T, D)
            embedding: Shape (F, T, E)
            initialization: Affiliations between 0 and 1. Shape (F, K, T)
            num_classes: Scalar >0
            iterations: Scalar >0
            saliency: Importance weighting for each observation, shape (F, T)
            hermitize:
            trace_norm:
            eigenvalue_floor:
            covariance_type: Either 'full', 'diagonal', or 'spherical'

        Returns:

        """
        assert xor(initialization is None, num_classes is None), (
            "Incompatible input combination. "
            "Exactly one of the two inputs has to be None: "
            f"{initialization is None} xor {num_classes is None}"
        )
        assert np.iscomplexobj(observation), observation.dtype
        assert np.isrealobj(embedding), embedding.dtype
        observation = observation / np.maximum(
            np.linalg.norm(observation, axis=-1, keepdims=True),
            np.finfo(observation.dtype).tiny,
        )

        F, T, D = observation.shape
        _, _, E = embedding.shape

        if initialization is None and num_classes is not None:
            affiliation_shape = (F, num_classes, T)
            initialization = np.random.uniform(size=affiliation_shape)
            initialization /= np.einsum("...kt->...t", initialization)[
                ..., None, :
            ]

        if saliency is None:
            saliency = np.ones_like(initialization[..., 0, :])

        quadratic_form = np.ones_like(initialization)
        affiliation = initialization
        for iteration in range(iterations):
            model = self._m_step(
                observation,
                embedding,
                quadratic_form,
                affiliation=affiliation,
                saliency=saliency,
                min_concentration=min_concentration,
                max_concentration=max_concentration,
                hermitize=hermitize,
                trace_norm=trace_norm,
                eigenvalue_floor=eigenvalue_floor,
            )

            if iteration < iterations - 1:
                affiliation, quadratic_form = model._predict(
                    observation=observation, embedding=embedding
                )

        return model

    def _m_step(
        self,
        observation,
        embedding,
        quadratic_form,
        affiliation,
        saliency,
        min_concentration,
        max_concentration,
        hermitize,
        trace_norm,
        eigenvalue_floor,
    ):
        F, T, D = observation.shape
        _, _, E = embedding.shape
        _, K, _ = affiliation.shape

        masked_affiliations = affiliation * saliency[..., None, :]
        weight = np.einsum("...kn->...k", masked_affiliations)
        weight /= np.einsum("...n->...", saliency)[..., None]

        embedding_ = np.reshape(embedding, (1, F * T, E))
        masked_affiliations_ = np.reshape(
            np.transpose(masked_affiliations, (1, 0, 2)),
            (K, F * T)
        )  # 'fkt->k,ft'
        vmf = VonMisesFisherTrainer()._fit(
            x=embedding_,
            saliency=masked_affiliations_,
            min_concentration=min_concentration,
            max_concentration=max_concentration
        )
        cacg = ComplexAngularCentralGaussianTrainer()._fit(
            x=observation[..., None, :, :],
            saliency=masked_affiliations,
            quadratic_form=quadratic_form,
            hermitize=hermitize,
            trace_norm=trace_norm,
            eigenvalue_floor=eigenvalue_floor,
        )
        return VMFCACGMM(weight=weight, vmf=vmf, cacg=cacg)
