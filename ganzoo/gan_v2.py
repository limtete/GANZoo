# coding=utf-8
# Copyright 2022, LIU Jialin.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Vanilla GANs. Trained by binary cross entropy with artifactual labels"""

import os
import math

from datetime import datetime
from absl import logging

import numpy as np
from PIL import Image
import tensorflow as tf
from tensorflow import keras
import tensorflow_datasets as tfds


try:
    # Module import
    from .context import _DATADIR, _WORKDIR, main
except ImportError:
    # Script import
    from context import _DATADIR, _WORKDIR, main


class GAN(keras.Model):
    """Generative Adversarial Networks (BCE version)
    """
    def __init__(self, name='gan_v2', **kwargs):
        super().__init__(name=name, **kwargs)
        # Image size
        self.h, self.w, self.c = 28, 28, 1
        self.img_shape = self.h * self.w * self.c

        # Training details
        self.lr = 1e-4
        self.epochs = 50
        self.batch_size = 32

        # Latent dim
        self.z_dim = 100

        # Build models
        self.generator = build_generator(self.img_shape, self.z_dim)
        self.discriminator = build_discriminator(self.img_shape, self.z_dim)

        # Working directory
        timeshift = datetime.today().strftime('%Y%m%d-%H:%M:%S')

        self.workdir = os.path.join(_WORKDIR, f'{name}/{timeshift}')
        self.logdir = os.path.join(self.workdir, 'logs')
        self.imgdir = os.path.join(self.workdir, 'imgs/')

        tf.io.gfile.makedirs(self.workdir)
        tf.io.gfile.makedirs(self.logdir)
        tf.io.gfile.makedirs(self.imgdir)

        # Save model architecture, not weights.
        # MNIST is a small dataset to reproduce.
        gjson = self.generator.to_json()
        djson = self.discriminator.to_json()

        with open(
            os.path.join(self.workdir, 'model.json'),
            mode='w',
            encoding='utf-8',
        ) as json_file:
            json_file.write(gjson)
            json_file.write(djson)

        # Keras Callbacks.
        self.callbacks = [
            SaveImage(workdir=self.imgdir, latent_dim=self.z_dim, interval=2),
            LearningRateDecay(init_lr=self.lr, decay_steps=self.epochs / 2.0),
            keras.callbacks.TensorBoard(log_dir=self.logdir),
        ]

        # Optimizer
        self.d_optimizer = keras.optimizers.Adam(
            learning_rate=self.lr, beta_1=0.5, beta_2=0.999
        )
        self.g_optimizer = keras.optimizers.Adam(
            learning_rate=self.lr, beta_1=0.5, beta_2=0.999
        )

        # Loss
        self.loss_fn = keras.losses.BinaryCrossentropy(from_logits=False)

        # Metric
        self.d_loss_metric = keras.metrics.Mean(name='d_loss')
        self.g_loss_metric = keras.metrics.Mean(name='g_loss')
        self.lr_metric = Monitor(name='d_lr')


    @property
    def metrics(self):
        return [
            self.d_loss_metric,
            self.lr_metric,
        ]

    # def train_step(self, data):
    #     imgs = data

    #     batch_size = tf.shape(imgs)[0]
    #     img = tf.reshape(imgs, shape=(batch_size, -1), name='flat_img')
    #     z = tf.random.uniform(
    #         shape=(batch_size, self.z_dim),
    #         minval=-1,
    #         maxval=1.,
    #         dtype=tf.float32,
    #     )

    #     img_ = self.generator(z)

    #     with tf.GradientTape(persistent=True) as tape:
    #         d_ins = tf.concat([img_, img], 0)
    #         d_outs = self.discriminator(d_ins)
    #         dg, dx = tf.split(d_outs, num_or_size_splits=2, axis=0)

    #         d_loss = tf.reduce_mean(tf.math.softplus(dg)) \
    #             + tf.reduce_mean(tf.math.softplus(-dx))
    #         g_loss = tf.reduce_mean(tf.math.softplus(-dg))

    #     d_gradients = tf.gradients(
    #         d_loss, self.discriminator.trainable_variables
    #     )
    #     g_gradients = tf.gradients(
    #         g_loss, self.generator.trainable_variables
    #     )

    #     self.d_optimizer.apply_gradients(
    #         zip(d_gradients, self.discriminator.trainable_variables)
    #     )
    #     self.g_optimizer.apply_gradients(
    #         zip(g_gradients, self.generator.trainable_variables)
    #     )

    #     self.d_loss_metric.update_state(d_loss)
    #     self.g_loss_metric.update_state(g_loss)
    #     self.lr_metric.update_state(self.d_optimizer.learning_rate)

    #     return {'d_loss': self.d_loss_metric.result(),
    #             'g_loss': self.g_loss_metric.result(),
    #             'd_lr':self.lr_metric.result(),}

    def train_step(self, data):
        img = data
        batch_size = tf.shape(img)[0]
        img = tf.reshape(img, shape=(batch_size, -1), name='flat_img')

        # Train Discriminator
        z = tf.random.normal(shape=(batch_size, self.z_dim))
        real = tf.zeros((batch_size, 1))
        fake = tf.ones((batch_size, 1))
        labels = tf.concat([fake, real], 0)
        # Important trick!
        labels += 0.05 * tf.random.uniform(tf.shape(labels))
        with tf.GradientTape() as tape:
            img_ = self.generator(z)
            imgs = tf.concat([img_, img], 0)

            d_outs = self.discriminator(imgs)
            dloss = self.loss_fn(labels, d_outs)

        d_grad = tape.gradient(
            dloss, self.discriminator.trainable_weights
        )
        self.d_optimizer.apply_gradients(
            zip(d_grad, self.discriminator.trainable_weights)
        )

        # Train Generator
        z = tf.random.normal(shape=(batch_size, self.z_dim))
        labels = tf.zeros((batch_size, 1))
        with tf.GradientTape() as tape:
            img_ = self.generator(z)
            d_out = self.discriminator(img_)

            gloss = self.loss_fn(labels, d_out)

        g_grad = tape.gradient(
            gloss, self.generator.trainable_weights
        )
        self.g_optimizer.apply_gradients(
            zip(g_grad, self.generator.trainable_weights)
        )

        self.d_loss_metric.update_state(dloss)
        self.g_loss_metric.update_state(gloss)
        self.lr_metric.update_state(self.d_optimizer.learning_rate)

        return {
            'dloss': self.d_loss_metric.result(),
            'lr': self.lr_metric.result(),
        }


def build_generator(
        img_shape,
        z_dim,
    ):
    """Generator Network"""
    x = keras.layers.Input(z_dim)
    y = keras.layers.Dense(
        256,
        # kernel_initializer=RandomNormal(mean=0.0, stddev=0.02),
        # kernel_regularizer=reg(),
    )(x)
    y = keras.layers.ReLU()(y)
    y = keras.layers.Dense(
        512,
        # kernel_initializer=RandomNormal(mean=0.0, stddev=0.02),
        # kernel_regularizer=reg(),
    )(y)
    y = keras.layers.ReLU()(y)
    y = keras.layers.BatchNormalization()(y)
    y = keras.layers.Dense(
        img_shape,
        # kernel_initializer=RandomNormal(mean=0.0, stddev=0.02),
        # kernel_regularizer=reg(),
        activation='tanh',
    )(y)
    return keras.Model(x, y, name='generator')

def build_discriminator(
        img_shape,
        z_dim,
    ):
    """Discriminator Network"""
    del z_dim
    x = keras.layers.Input(img_shape)
    y = keras.layers.Dense(
        512,
        # kernel_initializer=RandomNormal(mean=0.0, stddev=0.5),
        # kernel_regularizer=reg(),
    )(x)
    y = keras.layers.LeakyReLU(alpha=0.2)(y)
    y = keras.layers.Dense(
        256,
        # kernel_initializer=RandomNormal(mean=0.0, stddev=0.02),
        # kernel_regularizer=reg(),
    )(y)
    y = keras.layers.LeakyReLU(alpha=0.2)(y)
    y = keras.layers.BatchNormalization()(y)
    y = keras.layers.Dense(
        1,
        # kernel_initializer=RandomNormal(mean=0.0, stddev=0.02),
        # kernel_regularizer=reg(),
        activation='sigmoid',
    )(y)
    return keras.Model(x, y, name='discriminator')

class SaveImage(keras.callbacks.Callback):
    """Save image: callback function.
    """
    def __init__(self, workdir='.', interval=10, latent_dim=50):
        self.num_img = 100
        self.latent_dim = latent_dim
        self.workdir = workdir
        self.interval = interval

    def on_epoch_end(self, epoch, logs=None): # pylint: disable=unused-argument
        """Save at the end of #interval epochs.
        """
        if not tf.math.mod(epoch + 1, self.interval):
            # Latent vector
            z = tf.random.uniform(
                shape=(self.num_img, self.latent_dim),
                minval=-1.,
                maxval=1.
            )

            # Image generation
            image = self.model.generator.predict(z)

            nrow = 10
            padding = 2
            pad_value = 0.3

            ndarray = np.reshape(image, (100, 28, 28, 1))
            ndarray = np.concatenate((ndarray, ndarray, ndarray), -1)

            nmaps = ndarray.shape[0]
            xmaps = min(nrow, nmaps)
            ymaps = int(math.ceil(float(nmaps) / xmaps))
            height = int(ndarray.shape[1] + padding)
            width = int(ndarray.shape[2] + padding)
            num_channels = ndarray.shape[3]

            grid = np.full(
                (
                    height * ymaps + padding,
                    width * xmaps + padding,
                    num_channels
                ),
                pad_value,
            ).astype(np.float32)

            k = 0
            for y in range(ymaps):
                for x in range(xmaps):
                    if k >= nmaps:
                        break
                    grid[
                        y * height + padding:(y + 1) * height,
                        x * width + padding:(x + 1) * width
                    ] = ndarray[k]
                    k += 1

            image = 255 * (grid + 1) / 2
            image = np.clip(image, 0, 255)
            image = image.astype('uint8')

            tf.io.gfile.makedirs(self.workdir)
            Image.fromarray(image.copy()).save(
                os.path.join(self.workdir, f'G_z-{epoch + 1}.png')
            )

class LearningRateDecay(keras.callbacks.Callback):
    """Rate decay schedule: exponential decay.
    """
    def __init__(self, init_lr, decay_steps):
        self.lr = init_lr
        self.decay_rate = 0.1
        self.bound = decay_steps

    def on_epoch_begin(self, epoch, logs=None): # pylint: disable=unused-argument
        d_lr = self.model.d_optimizer.learning_rate
        g_lr = self.model.g_optimizer.learning_rate
        if epoch >= self.bound:
            weight = self.decay_rate ** (epoch / self.bound)
            d_lr.assign(self.lr * weight)
            g_lr.assign(self.lr * weight)

class Monitor(keras.metrics.Metric):
    """Learning rate monitor.
    """
    def __init__(self, name='lr_monitor', **kwargs):
        super().__init__(name=name, **kwargs)
        self.lr = tf.Variable(0.0, trainable=False)

    def update_state(self, lr):
        self.lr.assign(lr)

    def result(self):
        return self.lr

    def reset_state(self):
        self.lr.assign(0.0)


def get_mnist(data_dir=_DATADIR, batch_size=128):
    def norm_and_remove(img, label):
        """Normalize and Remove label

        Args:
            img (tf.Tensor): mnist image tensor
            label (tf.float32): mnist image label
        """
        del label
        return (tf.cast(img, tf.float32) - 127.5) / 127.5
        # return tf.cast(img, tf.float32) / 255.0

    (ds_train, ds_test), ds_info = tfds.load(
        'mnist',
        split=['train', 'test'],
        shuffle_files=True,
        with_info=True,
        as_supervised=True,
        data_dir=data_dir,
        try_gcs=False,
    )

    ds_train = ds_train.map(
        norm_and_remove,
        num_parallel_calls=tf.data.AUTOTUNE
    )
    ds_train = ds_train.cache()
    ds_train = ds_train.shuffle(ds_info.splits['train'].num_examples)
    ds_train = ds_train.batch(batch_size)
    ds_train = ds_train.prefetch(tf.data.AUTOTUNE)

    ds_test = ds_test.map(
        norm_and_remove,
        num_parallel_calls=tf.data.AUTOTUNE
    )
    ds_test = ds_test.cache()
    ds_test = ds_test.batch(batch_size)
    ds_test = ds_test.prefetch(tf.data.AUTOTUNE)

    return (ds_train, ds_test)

def test(argv):
    del argv

    model = GAN()

    model.generator.summary()
    model.discriminator.summary()

@main
def run(argv):
    """Train GAN on MNIST."""
    del argv

    # Environment variable setting
    os.environ['XLA_PYTHON_CLIENT_PREALLOCATE'] = 'false'
    os.environ['NO_GCE_CHECK'] = 'true'
    os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

    logging.set_verbosity(logging.DEBUG)

    model = GAN()

    ds_train, _ = get_mnist(batch_size=model.batch_size)

    model.compile(
        run_eagerly=False,
    )

    model.fit(
        ds_train,
        epochs=model.epochs,
        callbacks=model.callbacks,
    )

