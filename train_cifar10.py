import os
import numpy as np

import torch
import torch.optim as optim
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter

import data_loaders
import networks
import utils


################################################################################

# configurations
config = {
    'experiment_name': 'dfcvae_object_change_repetitive_T=50_10epoch',

    'N': 1000,
    'T': 50,
    'start_epoch': 0,
    'end_epoch': 10,
    'b_size': 256,
    'initial_lr': 0.001,
    'beta': 1.0, # parameter of KL term

    'log_file': 'log.txt',
    'load_saved': False
}


################################################################################

# create necessary directories
if not os.path.exists('experiments/'):
    os.makedirs('experiments')
root_dir = 'experiments/' + config['experiment_name']
if not os.path.exists(root_dir):
    os.makedirs(root_dir)
with open(os.path.join(root_dir, 'config.txt'), 'w') as f:
    for key in config:
        f.write(key + ': ' + str(config[key]) + '\n')
# use cpu or gpu
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# load data set and create data loader instance
print('Loading training data...')
ds = data_loaders.cifar10_loader_repetitive(config['N'], config['T'], True, 7)
train_loader = DataLoader(ds, batch_size=config['b_size']) # shuffle or not?

# model definition
model = networks.DFCVAE()
model.to(device=device)

# load saved models if load_saved flag is true
if config['load_saved']:
    model.load_state_dict(torch.load(os.path.join(root_dir, 'model')))

# optimizer definition
optimizer = optim.Adam(
    model.parameters(),
    lr = config['initial_lr']
)


# load_saved is false when training is started from 0th iteration
if not config['load_saved']:
    with open(os.path.join(root_dir, config['log_file']), 'w') as log:
        log.write('Epoch\tIteration\tReconstruction_loss\tStyle_KL\tContent_KL\n')
# initialize summary writer
writer = SummaryWriter()



# start training
for epoch in range(config['start_epoch'], config['end_epoch']):
    print()
    print('Epoch #' + str(epoch) + '........................................................')

    # the total loss at each epoch after running all iterations of batches
    total_loss = 0
    iteration = 0

    for batch_index, (X, y) in enumerate(train_loader):
        '''
        if iteration > config['end_iteration']:
            torch.save(model.state_dict(), os.path.join(root_dir, 'model'))
            exit()
        '''
        # set zero grad for the optimizer
        optimizer.zero_grad()

        # move data to cuda
        X = X.to(device=device)
        y = y.to(device=device)

        # style is individual, content is group
        style_mu, style_logvar, content_mu, content_logvar = model.encode(X)
        # put all content stuff into group in the grouping/evidence-accumulation stage
        group_mu, group_logvar = utils.accumulate_group_evidence(
            content_mu.data, content_logvar.data, y
        )

        # KL-divergence errors
        style_kl = -0.5*torch.sum(1+style_logvar-style_mu.pow(2)-style_logvar.exp())
        content_kl = -0.5*torch.sum(1+group_logvar-group_mu.pow(2)-group_logvar.exp())
        style_kl /= config['b_size'] * np.prod(ds.data_dim)
        content_kl /= config['b_size'] * np.prod(ds.data_dim)
        """
        sampling from group mu and logvar for each image in mini-batch differently makes
        the decoder consider content latent embeddings as random noise and ignore them 
        """
        # reconstruction error
        style_z = utils.reparameterize(training=True, mu=style_mu, logvar=style_logvar)
        content_z = utils.group_wise_reparameterize(
            training=True, mu=group_mu, logvar=group_logvar, labels_batch=y, cuda=True
        )
        reconstruction = model.decode(style_z, content_z)
        reconstruction_error = utils.mse_loss(reconstruction, X)
        # feature loss
        reconstruction_features = model.extract_features(reconstruction)
        input_features = model.extract_features(X)
        feature_loss = 0.0
        for (r, i) in zip(reconstruction_features, input_features):
            feature_loss += utils.mse_loss(r, i)

        # total_loss
        loss = (reconstruction_error+feature_loss) + config['beta']*(style_kl+content_kl)
        loss.backward()

        optimizer.step()

        total_loss += loss.detach()

        # print losses
        if (iteration+1) % 20 == 0:
            print('\tIteration #' + str(iteration))
            print('Reconstruction loss: ' + str(reconstruction_error.data.storage().tolist()[0]))
            print('Style KL loss: ' + str(style_kl.data.storage().tolist()[0]))
            print('Content KL loss: ' + str(content_kl.data.storage().tolist()[0]))
            print('Feature loss: ' + str(feature_loss.data.storage().tolist()[0]))
        iteration += 1

        # write to log
        with open(os.path.join(root_dir, config['log_file']), 'a') as log:
            log.write('{0}\t{1}\t{2}\t{3}\t{4}\n'.format(
                epoch,
                iteration,
                reconstruction_error.data.storage().tolist()[0],
                style_kl.data.storage().tolist()[0],
                content_kl.data.storage().tolist()[0]
            ))

        # write to tensorboard
        writer.add_scalar('Reconstruction loss', reconstruction_error.data.storage().tolist()[0],
                        epoch * (int(len(ds) / config['b_size']) + 1) + iteration)
        writer.add_scalar('Style KL', style_kl.data.storage().tolist()[0],
                        epoch * (int(len(ds) / config['b_size']) + 1) + iteration)
        writer.add_scalar('Content KL', content_kl.data.storage().tolist()[0],
                        epoch * (int(len(ds) / config['b_size']) + 1) + iteration)
        writer.add_scalar('Feature loss', feature_loss.data.storage().tolist()[0],
                        epoch * (int(len(ds) / config['b_size']) + 1) + iteration)

    writer.add_scalar('Total loss', total_loss.item(), epoch)
    print('\nTotal loss: ' + str(total_loss.item()))

    # save checkpoints after at every epoch
    torch.save(model.state_dict(), os.path.join(root_dir, 'model'))