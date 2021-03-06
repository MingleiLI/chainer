import math

import numpy

from chainer import cuda


# TODO(delta2323): Make it public function and move it to common directory.


def _sqnorm(x):
    if isinstance(x, cuda.GPUArray):
        with cuda.using_device(x):
            return float(cuda.gpuarray.dot(x, x).get())
    x = x.ravel()
    return float(x.dot(x))


class Optimizer(object):

    """Base class of all numerical optimizers.

    Optimizer is set up with references to parameters and gradients, and
    then on every call of :meth:`update`, it updates parameters based on
    corresponding gradients. Optimizer implementations must override
    :meth:`update_one` method, which updates one parameter array using the
    corresponding gradient array.

    Optimizer can optionally use state for each parameter/gradient pair. It is
    initialized by :meth:`init_state` method at set up.

    Attributes:
        t (int): Number of update steps. It can be used in :meth:`update_one`
            implementation, where :attr:`t` is incremented beforehand.

    """

    def setup(self, params_grads):
        """Prepares states for all given parameter/gradient pairs.

        Args:
            params_grads: Tuple (pair) of two tuples. The first element is a
                tuple of parameter arrays, and the second is a tuple of
                corresponding gradient arrays.
                Return value of :meth:`FunctionSet.collect_parameters` method
                can be used.

        """
        self.t = 0
        self.tuples = []
        for p, g in zip(*params_grads):
            with cuda.using_device(p):
                state = self.init_state(p, g)
                self.tuples.append((p, g, state))

    def init_state(self, param, grad):
        """Returns the initial state for given parameter and gradient.

        Default implementation delegates the procedure to
        :meth:`init_state_cpu` or :meth:`init_state_gpu` depending on the type
        of ``param``.

        Args:
            param: Parameter array.
            grad: Gradient array corresponding to ``param``.

        Returns:
            Initial state value.

            .. warning::

                Note that, on every call of :meth:`update_one`, the state value
                is passed by value and then the method updates its content, so
                the state must be a reference. Especiallly, one cannot use a
                value of built-in numeric type. If the state is one scalar
                value, it is recommended to use scalar array, i.e.
                :class:`~numpy.ndarray` with shape ``()``.

        """
        if isinstance(param, cuda.GPUArray):
            return self.init_state_gpu(param, grad)
        return self.init_state_cpu(param, grad)

    def init_state_cpu(self, param, grad):
        """Returns the initial state for given parameter and gradient on GPU.

        Args:
            param (~numpy.ndarray): Parameter array.
            grad  (~numpy.ndarray): Gradient array.

        Returns:
            Initial state value.

        .. seealso:: :meth:`init_state`, :meth:`init_state_gpu`

        """
        return None

    def init_state_gpu(self, param, grad):
        """Returns the initial state for given parameter and gradient on CPU.

        Args:
            param (~pycuda.gpuarray.GPUArray): Parameter array.
            grad  (~pycuda.gpuarray.GPUArray): Gradient array.

        Returns:
            Initial state value.

        .. seealso:: :meth:`init_state`, :meth:`init_state_gpu`

        """
        return None

    def zero_grads(self):
        """Fills all gradient arrays by zeros.

        This method should be call before backprop takes place, since
        gradients are accumulated on backprop.

        """
        for _, g, _ in self.tuples:
            if isinstance(g, cuda.GPUArray):
                with cuda.using_device(g):
                    g.fill(0)
            else:
                g.fill(0)

    def compute_grads_norm(self):
        """Computes the norm of whole gradients.

        Returns:
            float: L2 norm of whole gradients, i.e. square root of sum of
            square of all gradient elements.

        .. warning::

            This method returns a CPU-computed value, which means that this
            method synchronizes between CPU and GPU if at least one of the
            gradients reside on the GPU.

        """
        # TODO(beam2d): Make it asynchronous to CPU when gradients exist on GPU
        sqnorm = 0
        for _, g, _ in self.tuples:
            sqnorm += _sqnorm(g)
        return math.sqrt(sqnorm)

    def clip_grads(self, maxnorm):
        """Clips the norm of whole gradients up to given threshold.

        Args:
            maxnorm (float): Threshold of gradient L2 norm.

        .. seealso::

            :meth:`compute_grads_norm`
                It uses this method to compute the gradient norm to be clipped.

        """
        norm = self.compute_grads_norm()
        if norm > maxnorm:
            ratio = maxnorm / norm
            for _, g, _ in self.tuples:
                with cuda.using_device(g):
                    g *= ratio

    def weight_decay(self, decay):
        """Applies weight decay to the parameter/gradient pairs.

        Args:
            decay (float): Coefficient of weight decay

        """
        for p, g, _ in self.tuples:
            if isinstance(p, cuda.GPUArray):
                with cuda.using_device(p):
                    cuda.elementwise('float* g, const float* p, float decay',
                                     'g[i] += decay * p[i]',
                                     'weight_decay')(g, p, decay)
            else:
                g += decay * p

    def accumulate_grads(self, grads):
        """Accumulates gradients from other source.

        This method just adds given gradient arrays to gradients that this
        optimizer holds. It is typically used in data-parallel optimization,
        where gradients for different shards are computed in parallel and
        aggregated by this method. This method correctly treats multiple GPU
        devices.

        Args:
            grads (Iterable): Iterable of gradient arrays to be accumulated.

        """
        for (_, g_dst, _), g_src in zip(self.tuples, grads):
            if isinstance(g_dst, numpy.ndarray):
                g_dst += cuda.to_cpu(g_src)
                continue

            with cuda.using_device(g_dst):
                if (isinstance(g_src, cuda.GPUArray) and
                        g_dst.gpudata.device != g_src.gpudata.device):
                    g_dst += cuda.copy(g_src, out_device=g_src.gpudata.device)
                else:
                    g_dst += cuda.to_gpu(g_src)

    def update(self):
        """Updates all parameters and states using corresponding gradients.

        This method iteratively calls :meth:`update_one` for each parameter/
        gradient/state tuple. Beforehand, :attr:`t` attribute is incremented.

        """
        self.t += 1
        for p, g, s in self.tuples:
            with cuda.using_device(p):
                self.update_one(p, g, s)

    def update_one(self, param, grad, state):
        """Updates a parameter array and its state using given gradient.

        The default implementation delegates the procedure to
        :meth:`update_one_cpu` or :meth:`update_one_gpu` depending on the type
        of the parameter array. Optimizer implmentation must override these
        type-specific methods or this :meth:`update_one` method directly.

        Args:
            param: Parameter array.
            grad:  Gradient array.
            state: State value.

        .. seealso:: :meth:`update_one_cpu`, :meth:`update_one_gpu`

        """
        if isinstance(param, cuda.GPUArray):
            self.update_one_gpu(param, grad, state)
        else:
            self.update_one_cpu(param, grad, state)

    def update_one_cpu(self, param, grad, state):
        """Updates a parameter array and its state using given gradient on CPU.

        Args:
            param (~numpy.ndarray): Parameter array.
            grad  (~numpy.ndarray): Gradient array.
            state: State value.

        .. seealso:: :meth:`update_one`, :meth:`update_one_gpu`

        """
        raise NotImplementedError()

    def update_one_gpu(self, param, grad, state):
        """Updates a parameter array and its state using given gradient on GPU.

        Args:
            param (~pycuda.gpuarray.GPUArray): Parameter array.
            grad  (~pycuda.gpuarray.GPUArray): Gradient array.
            state: State value.

        .. seealso:: :meth:`update_one`, :meth:`update_one_cpu`

        """
        raise NotImplementedError()
